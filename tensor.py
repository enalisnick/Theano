"""A ResultBase to store numpy.ndarray with basic accompanying Ops"""

import numpy
from copy import copy
import inspect
from gof import ResultBase, Op, utils, Destroyer, Viewer, AbstractFunctionError

from base_tensor import BaseTensor
from elemwise import Elemwise


class Tensor(BaseTensor):
    """
    This subclass of BaseTensor provides operator overloading using implementations
    of Tensor operations contained in this file.
    
    Operators:
    - most numeric operators are overloaded (to return Ops that perform the
      corresponding calculation)
    """

    #UNARY
    def __abs__(self): return Abs(self).out
    def __neg__(self): return Neg(self).out

    #CASTS
    def __int__(self): return AsInt(self).out
    def __float__(self): return AsInt(self).out
    def __complex__(self): return AsComplex(self).out

    #COMPARISONS
    def __lt__(self,other): return lt(self, other)
    def __le__(self,other): return le(self, other)
    def __gt__(self,other): return gt(self, other)
    def __ge__(self,other): return ge(self, other)

    #ARITHMETIC - NORMAL
    def __add__(self,other): return add(self,other)
    def __sub__(self,other): return sub(self,other)
    def __mul__(self,other): return mul(self,other)
    def __div__(self,other): return div(self,other)
    def __pow__(self,other): return pow(self,other)

    #ARITHMETIC - INPLACE
    def __iadd__(self,other): return add_inplace(self,other)
    def __isub__(self,other): return sub_inplace(self,other)
    def __imul__(self,other): return mul_inplace(self,other)
    def __idiv__(self,other): return div_inplace(self,other)
    def __ipow__(self,other): return pow_inplace(self,other)

    #ARITHMETIC - RIGHT-OPERAND
    def __radd__(self,other): return add(other,self)
    def __rsub__(self,other): return sub(other,self)
    def __rmul__(self,other): return mul(other,self)
    def __rdiv__(self,other): return div(other,self)
    def __rpow__(self,other): return pow(other,self)

    #TRANSPOSE
    def __get_T(self):
        return tensor_copy(transpose(self))
    T = property(__get_T)

    #SLICING
    def __getitem__(self, key): raise NotImplementedError()
    def __getslice__(self, key): raise NotImplementedError()

# alternate Tensor constructor
def tinit(data, broadcastable=None, role=None, name=None):
    """Return a Tensor containing given data"""
    data = numpy.asarray(data)
    if broadcastable is None:
        broadcastable = [s==1 for s in data.shape]
    elif broadcastable in [0, 1]:
        broadcastable = [broadcastable] *  len(data.shape)
    rval = Tensor(data.dtype, broadcastable, role, name)
    rval.data = data # will raise if broadcastable was mis-specified
    return rval


############################
# Supporting Ops
############################

def _scalar_switch(normal_f, scalar_f, scalar_f_reverse = None):
    """a decorator for operators before broadcasting works properly"""
    def f(x, y):
        def as_tensor(obj):
            if isinstance(obj, Tensor):
                return obj
            else:
                return tinit(obj)
        x, y = as_tensor(x), as_tensor(y)
        if 0 not in y.broadcastable:
            return scalar_f(x, y)
        if 0 not in x.broadcastable:
            if scalar_f_reverse:
                return scalar_f_reverse(y, x)
            else:
                raise TypeError("You cannot do this operation on a scalar.")
        return normal_f(x, y)
    return f

def _assert_same_shapes(x, *rest):
    """Ensure that all inputs to the function impl have the same size (foils numpy's broadcasting)"""
    shape = x.shape
    for other in rest:
        if other.shape != shape:
            raise _assert_same_shapes.E_shape
_assert_same_shapes.E_shape = ValueError("The dimensions of the inputs do not match.")

def _assert_tensor_scalar(x, a):
    """ensure that the second input is a scalar"""
    if numpy.product(a.shape) != 1:
        raise ValueError("The second argument must be a scalar.")


class _Op(Op):
    """A convenient base for the ops in this file"""
    nin = -1
    nout = 1

    def __init__(self, *inputs):

        def as_tensor(obj):
            if isinstance(obj, Tensor):
                return obj
            else:
                return tinit(obj)
        inputs = map(as_tensor, inputs)
        
        if self.nin >= 0:
            if len(inputs) != self.nin:
                raise TypeError("Wrong number of inputs for %s (got %i, expected %i)") \
                    % (self, len(inputs), self.nin)

        i_broadcastables = [getattr(input, 'broadcastable', None) for input in inputs]
        i_dtypes = [getattr(input, 'dtype', None) for input in inputs]

        o_broadcastables = utils.from_return_values(self.propagate_broadcastable(*i_broadcastables))
        o_dtypes = utils.from_return_values(self.propagate_dtype(*i_dtypes))

        self.inputs = inputs
        self.outputs = [Tensor(dtype, broadcastable) for broadcastable, dtype in zip(o_broadcastables, o_dtypes)]

    def propagate_broadcastable(self, *inputs):
        raise AbstractFunctionError()
    
    def propagate_dtype(self, *i_dtypes):
        def upcast(dtype, *dtypes):
            z = numpy.zeros((), dtype = dtype)
            #print '----', self.__class__
            #print type(z), dtype
            for dtype in dtypes:
                z = z + numpy.zeros((), dtype = dtype)
                #print type(z), type(dtype), dtype
            return str(z.dtype)
        for dtype in i_dtypes:
            if dtype is None:
                raise TypeError("Expected a Tensor.")
        rval = upcast(*i_dtypes)
        return rval
    
    def impl(self, *inputs):
        raise AbstractFunctionError()
    
    def perform(self):
        res = self.impl(*[input.data for input in self.inputs])
        if self.nout == 1:
            self.outputs[0].data = res
        else:
            for output, value in zip(self.outputs, res):
                output.data = value
    
    def c_var_names(self):
        (self, inames, onames), _1, _2, _3 = inspect.getargspec(self.c_impl)
        inames = utils.from_return_values(inames)
        onames = utils.from_return_values(onames)
        return [inames, onames]
    
    def c_code(self):
        return self.c_impl(self.inputs, self.outputs)

    def c_impl(self, inputs, outputs):
        raise AbstractFunctionError()

class _Unary:
    nin = 1

class _Binary:
    nin = 2


class _Elemwise(Elemwise, _Op):

    @staticmethod
    def extract_name(name):
        if name.endswith("_i"):
            return name[:-2]
        else:
            return name
    
    @staticmethod
    def is_loop_var(name):
        return name.endswith("_i")

    def var_desc(self):
        cls = self.__class__
        (self, inames, onames), _1, _2, _3 = inspect.getargspec(cls.c_foreach)
        return ([(cls.extract_name(name), cls.is_loop_var(name)) for name in inames],
                [(cls.extract_name(name), cls.is_loop_var(name)) for name in onames])

    def propagate_broadcastable(self, *inputs):
        idesc, odesc = self.var_desc()
        nonloop_o = [o[0] for o in odesc if not o[1]]
        if nonloop_o:
            raise Exception("Cannot infer broadcastable for non-loop variable(s) %s" % nonloop_o)
        all_bcast = [broadcastable for broadcastable, i in zip(inputs, idesc) if i[1]]
        if reduce(lambda x, y: x is not False and x == y and y, [len(x) for x in all_bcast]) is False:
            raise TypeError(_Elemwise.propagate_broadcastable.E_ndim, self.__class__)
        ret = []
        for arr in zip(*all_bcast):
            if 0 in arr:
                ret.append(0)
            else:
                ret.append(1)
        return [ret] * self.nout
    propagate_broadcastable.E_ndim \
            = "Inputs that are loop variables do not all have the same number of dimensions."

    def c_init(self, inputs, outputs):
        raise AbstractFunctionError()        

    def c_foreach(self, inputs, outputs):
        raise AbstractFunctionError()

    def c_finalize(self, inputs, outputs):
        raise AbstractFunctionError()

    def c_code_init(self):
        return self.c_init(self.inputs, self.outputs)

    def c_code_foreach(self):
        return self.c_foreach(self.inputs, self.outputs)

    def c_code_finalize(self):
        return self.c_finalize(self.inputs, self.outputs)


class TensorScalarOp(_Elemwise):
    def var_desc(self):
        return [('x', 1), ('a', 0)], [('z', 1)]
    def c_code_init(self):
        return """
        if (PyArray_SIZE(%(a)s) != 1) {
            PyErr_SetString(PyExc_ValueError, \"The size of the scalar argument is not 1.\");
            %(fail)s
        }
        dtype_%(a)s _%(a)s = ((dtype_%(a)s*)PyArray_DATA(%(a)s))[0];
        """
    def c_code_foreach(self):
        return "%%(z)s_i = %s;" % self.c_expr

def _constructor(op_cls):
    """Return a function that calls op_cls(*input)
    and returns the outputs of the op (with single outputs unpacked)
    """
    def f(*args, **kwargs):
        op = op_cls(*args, **kwargs)
        if len(op.outputs) > 1:
            return op.outputs
        else:
            return op.outputs[0]
    return f


##########################
# Unary Operations
##########################

class Abs(_Elemwise):
    def impl(self, x):
        return numpy.abs(x)
    def grad(self, x, gz):
        return gz * Sgn(x).out #TODO: handle the corner case (get it? pun?) (there's a special place in hell for people like you)
    def c_foreach(self, (x_i, ), (z_i, )):
        return "%(z)s_i = abs(%(x)s_i);"
#Constructor not necessary because builtin abs() does this

class Exp(_Elemwise):
    def impl(self, x): return numpy.exp(x)
    def grad(self, x, gz): return gz * exp(x)
    def c_foreach(self, (x_i, ), (z_i, )): return "z_i = exp(x_i);"
exp = _constructor(Exp)

class Neg(_Elemwise):
    def impl(self, x):
        return -x
    def grad(self, x, gz):
        return -gz
    def c_foreach(self, (x_i, ), (z_i, )):
        return "%(z)s_i = -%(x)s_i;"
#Constructor not necessary because unary '-' does this

class Log(_Elemwise):
    def impl(self, x): return numpy.log(x)
    def grad(self, x, gz): return gz / x
    def c_foreach(self, (x_i, ), (z_i, )): return "z_i = log(x_i);"
log = _constructor(Log)

class Sgn(_Elemwise):
    def impl(self, x):
        return numpy.abs(x) / x
    def grad(self, x, gz):
        return [None]
    def c_foreach(self, (x_i, ), (z_i, )):
        return "%(z)s_i = %(x)s_i/abs(%(x)s_i);" # TODO: C use copysign
sgn = _constructor(Sgn)

class Sum(_Elemwise):
    def impl(self, x):
        return numpy.sum(x)
    def grad(self, x, gz):
        return fill(x, gz)
    def propagate_broadcastable(self, *inputs):
        return [()]
    def c_init(self, (x, ), (sum, )):
        return "dtype_%(sum)s* %(sum)sp = ((dtype_%(sum)s*)PyArray_DATA(%(sum)s)); %(sum)sp[0] = 0;"
    def c_foreach(self, (x_i, ), (sum, )):
        return "%(sum)sp[0] += %(x)s_i;"
sum = _constructor(Sum)

class Fill(_Elemwise):
    def impl(self, model, value):
        return (model * 0) + value #TODO: we can probably do better than this
    def grad(self, (model, value), gz):
        return None, sum(gz)
    def c_init(self, (model, value), (z, )):
        return "dtype_%(value)s %(value)s0 = ((dtype_%(value)s*)PyArray_DATA(%(value)s))[0];"
    def c_foreach(self, (model_i, value), (z_i, )):
        return "%(z)s_i = %(value)s0;"
fill = _constructor(Fill)


class TensorCopy(_Elemwise):
    def impl(self, x):
        return numpy.array(x)
    def grad(self, x, gz):
        return gz
    def c_foreach(self, (x_i, ), (z_i, )):
        return "%(z)s_i = %(x)s_i;"
tensor_copy = _constructor(TensorCopy)

if 0:
    ##########################
    # View Operations
    ##########################

    class transpose(_Op, Viewer):
        def view_map(self):
            return {self.out: [self.inputs[0]]}
        def impl(self, x):
            return x.T
        def grad(self, x, gz):
            return transpose_copy(gz)
        def propagate_broadcastable(self, x):
            rval = list(x)
            rval.reverse()
            return [rval]
        
        def c_impl(self, x, z):
            return """
            PyArrayObject* transposed = (PyArrayObject*)PyArray_Transpose(%(x)s, NULL);
            if (%(z)s) {
                Py_XDECREF(%(z)s);
            }
            %(z)s = transposed;
            """

    class Subtensor(_Op, Viewer):
        def view_map(self): 
            return {self.out: [self.inputs[0]]}
        def impl(x, item): 
            rval = x.__getitem__(item)
            #print 'get_slice running', rval
            return rval
        def grad(x, gz):
            # - option: allocate a potentially large matrix of zeros, and fill in
            # the appropriate elements from gz
            # - option: return a sparse matrix
            # - option: return gz, but think about how to include a special addition
            # function that uses a matching view over the original data
            raise NotImplemented 


##########################
# Arithmetic : Add
##########################

# Elemwise #
class AddElemwise(_Elemwise):
    def impl(self, x, y):
        _assert_same_shapes(x, y)
        return x + y
    def grad(self, (x, y), gz):
        return gz, gz
    def c_foreach(self, (x_i, y_i), (z_i, )):
        return "z_i = x_i + y_i;"
add_elemwise = _constructor(AddElemwise)

class AddElemwiseInplace(AddElemwise.inplace_version()):
    def impl(self, x, y):
        _assert_same_shapes(x, y)
        x += y
        return x
add_elemwise_inplace = _constructor(AddElemwiseInplace)

# Scalar #
class AddScalar(TensorScalarOp):
    def impl(self, x, a):
        _assert_tensor_scalar(x, a)
        return x + a
    def grad(self, (x, a), gz):
        return gz, sum(gz)
    c_expr = "x_i + a"
add_scalar = _constructor(AddScalar)

class AddScalarInplace(AddScalar.inplace_version()):
    def impl(self, x, a):
        _assert_tensor_scalar(x, a)
        x += a
        return x
add_scalar_inplace = _constructor(AddScalarInplace)

add = _scalar_switch(add_elemwise, add_scalar, add_scalar)
add_inplace = _scalar_switch(add_elemwise_inplace, add_scalar_inplace)


##########################
# Arithmetic : Sub
##########################

# Elemwise #
class SubElemwise(_Elemwise):
    def impl(self, x, y):
        _assert_same_shapes(x, y)
        return x - y
    def grad(self, (x, y), gz):
        return gz, -gz
    def c_foreach(self, (x_i, y_i), (z_i, )):
        return "z_i = x_i - y_i;"
sub_elemwise = _constructor(SubElemwise)

class SubElemwiseInplace(SubElemwise.inplace_version()):
    def impl(self, x, y):
        _assert_same_shapes(x, y)
        x -= y
        return x
sub_elemwise_inplace = _constructor(SubElemwiseInplace)

# Scalar #
def sub_scalar_r(x, a):
    return add_scalar(x, -a)

def sub_scalar_l(x, a):
    return add_scalar(-x, a)

def sub_scalar_rinplace(x, a):
    return add_scalar_inplace(x, -a)

sub = _scalar_switch(sub_elemwise, sub_scalar_r, sub_scalar_l)
sub_inplace = _scalar_switch(sub_elemwise_inplace, sub_scalar_rinplace)

##########################
# Arithmetic : Mul
##########################

# Elemwise #
class MulElemwise(_Elemwise):
    def impl(self, x, y):
        _assert_same_shapes(x, y)
        return x * y
    def grad(self, (x, y), gz):
        return mul(y, gz), mul(x, gz)
    def c_foreach(self, (x_i, y_i), (z_i, )):
        return "%(z)s_i = %(x)s_i * %(y)s_i;"
mul_elemwise = _constructor(MulElemwise)

class MulElemwiseInplace(MulElemwise.inplace_version()):
    def impl(self, x, y):
        _assert_same_shapes(x, y)
        x *= y
        return x
mul_elemwise_inplace = _constructor(MulElemwiseInplace)

# Scalar #
class Scale(TensorScalarOp):
    def impl(self, x, a):
        _assert_tensor_scalar(x, a)
        return x * a
    def grad(self, (x, a), gz):
        return scale(a, gz), sum(mul_elemwise(x, gz))
    c_expr = "%(x)s_i * _%(a)s"
scale = _constructor(Scale)

class ScaleInplace(Scale.inplace_version()):
    def impl(self, x, a):
        _assert_tensor_scalar(x, a)
        x *= a
        return x
scale_inplace = _constructor(ScaleInplace)

mul = _scalar_switch(mul_elemwise, scale, scale)
mul_inplace = _scalar_switch(mul_elemwise_inplace, scale_inplace)


##########################
# Arithmetic : Div
##########################

# Elemwise #
class DivElemwise(_Elemwise):
    def impl(self, x, y):
        _assert_same_shapes(x, y)
        return x / y
    def grad(self, (x, y), gz):
        return div(gz, y), -div(mul(x, gz), (y*y))
    def c_foreach(self, (x_i, y_i), (z_i, )):
        return "%(z)s_i = %(x)s_i / %(y)s_i;"
div_elemwise = _constructor(DivElemwise)

class DivElemwiseInplace(DivElemwise.inplace_version()):
    def impl(self, x, y):
        _assert_same_shapes(x, y)
        x /= y
        return x
div_elemwise_inplace = _constructor(DivElemwiseInplace)

class InvElemwise(_Elemwise):
    def impl(self, x):
        return 1.0/x
    def grad(self, x, gz):
        return -gz / (x*x)
    def c_foreach(self, (x_i, ), (z_i, )):
        return "%(z)s_i = 1.0 / %(x)s_i;" #TODO: cast 1.0 to the dtype of x
inv_elemwise = _constructor(InvElemwise)

# Scalar #
def div_scalar_r(x, a):
    return scale(x, inv_elemwise(a))

def div_scalar_l(x, a):
    return scale(inv_elemwise(x), a)

def div_scalar_rinplace(x, a):
    return scale_inplace(x, inv_elemwise(a))

div = _scalar_switch(div_elemwise, div_scalar_r, div_scalar_l)
div_inplace = _scalar_switch(div_elemwise_inplace, div_scalar_rinplace)




##########################
# Arithmetic : Pow
##########################

# Elemwise #

class PowElemwise(_Elemwise):
    def impl(self, x, y):
        _assert_same_shapes(x, y)
        return x ** y
    def grad(self, (x, y), gz):
        gx = gz * y * (pow_elemwise(x, y-1.0))
        gs = gz * log(x) * pow_elemwise(x, y)
        return gx, gs
    def c_foreach(self, (x_i, y_i), (z_i, )):
        return "%(z)s_i = pow(%(x)s_i, %(y)s_i);"
pow_elemwise = _constructor(PowElemwise)

class PowElemwiseInplace(PowElemwise.inplace_version()):
    def impl(self, x, y):
        _assert_same_shapes(x, y)
        x **= y
        return x
pow_elemwise_inplace = _constructor(PowElemwiseInplace)

# Scalar #
class PowScalarL(TensorScalarOp):
    def impl(self, x, a):
        _assert_tensor_scalar(x, a)
        return a ** x
    def grad(self, (x, s), gz):
        gx = sum(gz * s * pow_scalar_l(add_scalar(s,-1.0), x))
        gs = scale(mul(gz, pow_scalar_l(s, x)), log(x))
        return gx, gs
    c_expr = "pow(%(a)s, %(x)s_i)"
pow_scalar_l = _constructor(PowScalarL)

class PowScalarR(TensorScalarOp):
    def impl(self, x, a):
        _assert_tensor_scalar(x, a)
        return x ** a
    def grad(self, (x, s), gz):
        gx = scale(mul_elemwise(gz,pow_scalar_r(x, add_scalar(s,-1.0))), s)
        gs = sum(mul_elemwise(mul_elemwise(gz, pow_scalar_r(x,s)), log(x)))
        return gx, gs
    c_expr = "pow(%(x)s_i, _%(a)s)"
pow_scalar_r = _constructor(PowScalarR)

class PowScalarRInplace(PowScalarR.inplace_version()):
    def impl(self, x, a):
        _assert_tensor_scalar(x, a)
        x **= a
        return x
pow_scalar_r_inplace = _constructor(PowScalarRInplace)

pow = _scalar_switch(pow_elemwise, pow_scalar_r, pow_scalar_l)
pow_inplace = _scalar_switch(pow_elemwise_inplace, pow_scalar_r_inplace)


if 0:
    ##########################
    # Comparisons 
    ##########################

    # Less-than
    class lt_elemwise(_Elemwise):
        def __init__(self, *args):
            raise NotImplementedError()
    class lt_scalar_r(_Elemwise):
        def __init__(self, *args):
            raise NotImplementedError()
    # Less-than or equal
    class le_elemwise(_Elemwise):
        def __init__(self, *args):
            raise NotImplementedError()
    class le_scalar_r(_Elemwise):
        def __init__(self, *args):
            raise NotImplementedError()
    # Greater-than or equal
    class gt_elemwise(_Elemwise):
        def __init__(self, *args):
            raise NotImplementedError()
    class gt_scalar_r(_Elemwise):
        def __init__(self, *args):
            raise NotImplementedError()
    # Greater-than or equal
    class ge_elemwise(_Elemwise):
        def __init__(self, *args):
            raise NotImplementedError()
    class ge_scalar_r(_Elemwise):
        def __init__(self, *args):
            raise NotImplementedError()




if 0:
    def _broadcastable_pattern(pattern):
        def factory(data = None, name = None, dtype=None):
            if data: 
                assert len(data.shape) == len(pattern)
                if dtype is not None:
                    assert dtype is data.dtype
                dtype = data.dtype
                rval = Tensor(dtype, pattern, name)
                rval.data = data
            else:
                rval = Tensor(dtype, pattern, name)
            return  rval
        return factory

    row = _broadcastable_pattern([1, 0])
    col = _broadcastable_pattern([0, 1])
    matrix = _broadcastable_pattern([0, 0])

if 0: #old __init__ code
    """Create a Tensor

    If data is given:
        - constant defaults to True
        - if dtype is given, it must match data.dtype
            - otherwise: default is data.dtype
        - if broadcastable is given, len(broadcastable) must match len(data.shape)
            - otherwise: if it is constant, it defaults to 1 where shape[i]==1
            - if it is not constant, it defaults to 0s

    If data is not given:
        - constant defaults to False
    """
    if dtype is None or broadcastable is None:
        if data is None:
            raise TypeError("Provide non-None data to complete the dtype and broadcastable flags.")
        data = numpy.asarray(data)
        if constant is None:
            constant = True
        dtype = data.dtype
        if constant:
            broadcastable = [1*(x == 1) for x in data.shape]
        else:
            broadcastable = [0] * len(data.shape)

if 0:
    def tensor__new__(cls, *args, **kwargs):
        """__new__ is overloaded to handle the special form Tensor(x) when x is
        a Tensor or an Op whose default output is a Tensor.  In these cases, the
        argument x is returned, and a new Tensor is not created.
        """
        if len(args) == 1:
            a = args[0]

        t = super(Tensor, cls).__new__(cls, *args, **kwargs)
        t.__init__(*args, **kwargs)
        return t


