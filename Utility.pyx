# cython: language_level = 3str
# cython: embedsignature = True
# cython: boundscheck = False
# cython: wraparound = False
# cython: cdivision = True
import numpy as np
cimport numpy as np
from libc.math cimport fmax, ceil, sqrt, cbrt, sin, cos

cpdef tuple interpolateGridData(np.ndarray[np.float_t] x, np.ndarray[np.float_t] y, np.ndarray val, np.ndarray z=None,
                                double mesh_target=1e4, str interp="linear", double fill_val=np.nan):
    """
    Interpolate given coordinates and field properties to satisfy given summed mesh size, with given interpolation method.
    If z is not given, then the interpolation is 2D. 
    The number of cells nx, ny, (nz) in each dimension is automatically determined to scale with physical dimension length lx, ly, (lz),
    so that nx = lx*nbase; ny = ly*nbase; (nz = lz*nbase), 
    and nx*ny = mesh_target or nx*ny*nz = mesh_target. 
    
    :param x: X coordinates.
    :type x: ndarray[n_points]
    :param y: Y coordinates.
    :type y: ndarray[n_points]
    :param val: n_features number of field properties to interpolate with mesh.
    :type val: ndarray[n_points, n_features] if n_features > 1 or ndarray[n_points]
    :param z: Z coordinates in case of 3D.
    :type z: ndarray[n_points] or None, optional (default=None)
    :param mesh_target: Summed size of the target mesh. 
    The function takes this value and assign cells to each dimension based on physical dimension length.
    :type mesh_target: float, optional (default=1e4)
    :param interp: Interpolation method used by scipy.interpolate.griddata.
    :type interp: "nearest" or "linear" or "cubic", optional (default="linear")
    :param fill_val: Value to replace NaN.
    :type fill_val: float, optional (default=nan)

    :return: X, Y, Z mesh grid and field properties mesh grid.
    If Z was not given, then Z is returned as dummy array.
    Field properties values are stacked as the last dimension, either 3rd (2D grid) or 4th (3D grid) dimension.
    :rtype: (ndarray[nx, ny], ndarray[nx, ny], empty(1), ndarray[nx, ny, n_features])
    or (ndarray[nx, ny, nz], ndarray[nx, ny, nz], ndarray[nx, ny, nz], ndarray[nx, ny, nz, n_features])
    """
    from scipy.interpolate import griddata

    cdef tuple shape_val = np.shape(val)
    cdef double lx, ly, lz, nbase
    cdef int nx, ny, nz, i, n_features
    cdef np.ndarray[np.float_t, ndim=2] coor_known
    cdef np.ndarray xmesh, ymesh, zmesh, val_mesh
    cdef tuple coor_request
    cdef double complex precision_x, precision_y, precision_z

    print('\nInterpolating data to target mesh size ' + str(mesh_target) + ' with ' + str(interp) + ' method...')
    # Ensure val is at least 2D with shape (n_points, 1) if it was 1D
    if len(shape_val) == 1:
        val = np.transpose(np.atleast_2d(val))

    n_features = val.shape[1]
    # Get x, y, z's length, and prevent 0 since they will be divided later
    lx, ly = fmax(x.max() - x.min(), 0.0001), fmax(y.max() - y.min(), 0.0001)
    lz = fmax(z.max() - z.min(), 0.0001) if z is not None else 0.

    # Since we want number of cells in x, y, z to scale with lx, ly, lz, create a base number of cells nbase and
    # let (lx*nbase)*(ly*nbase)*(lz*nbase) = mesh_target,
    # then nbase = [mesh_target/(lx*ly*lz)]^(1/3)
    nbase = cbrt(mesh_target/(lx*ly*lz)) if z is not None else sqrt(mesh_target/(lx*ly))

    nx, ny = <int>ceil(lx*nbase), <int>ceil(ly*nbase)
    if z is not None: nz = <int>ceil(lz*nbase)
    precision_x = nx*1j
    precision_y = ny*1j
    if z is not None: precision_z = nz*1j

    if z is not None:
        # Known coordinates with shape (3, n_points) trasposed to (n_points, 3)
        coor_known = np.transpose(np.vstack((x, y, z)))
        xmesh, ymesh, zmesh = np.mgrid[x.min():x.max():precision_x,
                              y.min():y.max():precision_y,
                              z.min():z.max():precision_z]
        coor_request = (xmesh, ymesh, zmesh)
        val_mesh = np.empty((xmesh.shape[0], xmesh.shape[1], xmesh.shape[2], n_features))
    else:
        coor_known = np.transpose(np.vstack((x, y)))
        xmesh, ymesh = np.mgrid[x.min():x.max():precision_x,
                       y.min():y.max():precision_y]
        # Dummy array for zmesh in 2D
        zmesh = np.empty(1)
        coor_request = (xmesh, ymesh)
        val_mesh = np.empty((xmesh.shape[0], xmesh.shape[1], n_features))

    # Interpolate for each value column
    for i in range(n_features):
        print('\n Interpolating value ' + str(i + 1) + '...')
        if z is not None:
            val_mesh[:, :, :, i] = griddata(coor_known, val[:, i], coor_request, method=interp, fill_value=fill_val)
        else:
            val_mesh[:, :, i] = griddata(coor_known, val[:, i], coor_request, method=interp, fill_value=fill_val)

    # In case provided value only has 1 feature, compress from shape (grid mesh, 1) to (grid mesh)
    if n_features == 1:
        if z is None:
            val_mesh = val_mesh.reshape((val_mesh.shape[0], val_mesh.shape[1]))
        else:
            val_mesh = val_mesh.reshape((val_mesh.shape[0], val_mesh.shape[1], val_mesh.shape[2]))

    print('\nValues interpolated to mesh ' + str(np.shape(xmesh)))
    return xmesh, ymesh, zmesh, val_mesh


cpdef tuple collapseMeshGridFeatures(np.ndarray meshgrid, bint infer_matrix_form=True, tuple matrix_shape=(3, 3), bint collapse_matrix=True):
    """
    Collapse a given value meshgrid of [grid shape x feature shape] to array of [n_points x feature shape] or [n_points x n_features].
    At least the last D has to be feature(s).
    When infer_matrix_form is disabled, 
        If meshgrid >= 3D, the 1st (n - 1)D are always collapsed to 1, with last D (features) unchanged. 
        Else, meshgrid is assumed (n_points, n_features) and the original meshgrid is returned.
    When infer_matrix_form is enabled, 
        if last len(matrix_shape)D shape is matrix_shape, the first (n - len(matrix_shape)D are collapsed to 1D, 
            and if collapse_matrix is enabled, collapse the matrix shape to 1D too;
        else if last len(matrix_shape)D shape is not matrix_shape, assume last D is features and collapse the 1st (n - 1)D to 1D.
        
    Typical examples:
        Meshgrid is (nx, ny, nz, 3, 3). 
            If infer_matrix_form and matrix_shape is (3, 3):
                Array is (nx*ny*nz, 3*3) if collapse_matrix is True else (nx*ny*nz, 3, 3).
            Else, array is (nx*ny*nz*3, 3).
        Meshgrid is (n_points, 10, 3, 3).
            If infer_matrix_form and matrix_shape is (10, 3, 3):
                Array is (n_points, 10*3*3) if collapse_matrix else (n_points, 10, 3, 3).
            Else, array is (n_points*10*3, 3).
        Meshgrid is (nx, ny, n_features).
            nz is assumed feature D instead of spatial D and array is (nx*ny, n_features).
            In case of (nx, ny, nz) 3D meshgrid, please use meshgrid.ravel(). 
        Meshgrid is (n_points, n_features).
            Nothing is done.

    :param meshgrid: Meshgrid of multiple features to collapse. At least last D should be feature(s).
    :type meshgrid: ndarray[grid_shape x feature shape]
    :param infer_matrix_form: Whether to infer if given meshgrid has features in the form matrix of shape matrix_shape. 
    If True and if last len(matrix_shape)D shape is matrix_shape, then grid shape of meshgrid excludes matrix_shape.
    :type infer_matrix_form: bool, optional (default=True)
    :param matrix_shape: If infer_matrix_form is True, shape of matrix to infer.
    :type matrix_shape: tuple, optional (default=(3, 3))
    :param collapse_matrix: If infer_matrix_form is True, whether to collapse matrix_shape to 1D, 
    once such matrix_shape has found in given meshgrid.
    :type collapse_matrix: bool, optional (default=True)

    :return: Meshgrid of multiple features collapsed to either (n_points, n_features) or (n_points, feature shape); and its original shape.
    :rtype: (ndarray[n_points x n_features] or ndarray[n_points x feature shape], tuple)
    """
    cdef tuple shape_old, shape_grid
    cdef np.ndarray arr
    cdef int matrix_nelem = 1
    cdef int grid_nelem = 1
    cdef int i
    cdef list shape_new

    shape_old = np.shape(meshgrid)
    # Go throuhg each matrix D to calculate number of matrix elements
    for i in range(len(matrix_shape)):
        matrix_nelem *= matrix_shape[i]

    # If infer_matrix_form
    # and at least one more D than len(matrix_shape)
    # and the last len(matrix_shape) D is given matrix_shape
    if infer_matrix_form and \
            len(shape_old) >= len(matrix_shape) + 1 and \
            shape_old[(len(shape_old) - len(matrix_shape)):len(shape_old)] == matrix_shape:
        shape_grid = shape_old[:(len(shape_old) - len(matrix_shape))]
        # Go through each grid D and find out number of grid elements
        for i in range(len(shape_grid)):
            grid_nelem *= shape_grid[i]

        # If collapse_matrix is True, then new array is collapsed both in grid D and matrix form D
        if collapse_matrix:
            arr = meshgrid.reshape((grid_nelem, matrix_nelem))
        # Else only collapsed in grid D and keep matrix form
        else:
            shape_new = [grid_nelem] + list(matrix_shape)
            arr = meshgrid.reshape(tuple(shape_new))

    # Else if either infer_matrix_form is False
    # and/or old array D <= len(matrix_shape) but >= 3
    # and/or old array's last len(matrix_shape) D is not given matrix_shape.
    # Note this inherently assumes the 3rd D is feature instead of z if given meshgrid is 3D
    elif len(shape_old) >= 3:
        shape_grid = shape_old[:(len(shape_old) - 1)]
        # Go through each grid dimension
        for i in range(len(shape_grid)):
            grid_nelem *= shape_grid[i]

        # New array is collapsed in grid and last D of original shape is kept
        arr = meshgrid.reshape((grid_nelem, shape_old[len(shape_old) - 1]))
    # Else if meshgrid is 2D, assume (n_points, n_features) and don't change anything
    else:
        arr = meshgrid

    print('\nMesh grid of multiple features collapsed from shape ' + str(shape_old) + ' to ' + str(np.shape(arr)))
    return arr, shape_old


cpdef np.ndarray reverseOldGridShape(np.ndarray arr, tuple shape_old, bint infer_matrix_form=True, tuple matrix_shape=(3, 3)):
    """
    Reverse the given array to the old grid (but not value) shape, given the old array (both grid and value) shape.
    If infer_matrix_form is enabled, then the last 2D of given array will be checked whether it matches given matrix_shape. 
    If so, then grid shape is inferred to exclude the last 2D. Otherwise, grid shape is inferred to exclude the last D.
    If infer_matrix_form is disabled, then the given array is simply reversed/reshaped to the given shape (both grid and value shape).
    
    :param arr: Array to reverse shape. The last D or last 2D are considered non-grid related values and will not be touched.
    :type arr: ndarray[:, ..., :, 3, 3] or ndarray[:, ..., :]
    :param shape_old: Old full shape (first grid dimensions then value dimensions) used to reverse to old grid.
    If infer_matrix_form is False, then old shape's value dimensions are also used for array reshape.
    :type shape_old: tuple
    :param infer_matrix_form: Whether to detect for matrix form of matrix_shape in the last 2D of arr and shape_old,
    to infer value shape of arr and grid shape of shape_old.
    :type infer_matrix_form: bool optional (default=True)
    :param matrix_shape: If in_matrix_form is True, the matrix shape to detect.
    :type matrix_shape: tuple[:, :], optional (default=(3, 3))
     
    :return: Array reversed/reshaped to detected old grid or shape_old if infer_matrix_form is disabled.
    :rtype: ndarray[old grid x matrix_shape] or ndarray[old grid x values] or ndarray[shape_old]
    """
    cdef tuple shape_arr
    cdef list shape_old_grid

    shape_arr = np.shape(arr)
    # If inferring matrix form, then check if last 2D is a matrix_shape
    if infer_matrix_form:
        # Deduce the old grid shape
        # If the last 2D is matrix_shape then shape_old is a matrix_shape tensor
        if shape_old[len(shape_old) - 2:] == matrix_shape:
            shape_old_grid = list(shape_old[:len(shape_old) - 2])
        # Otherwise, only the last D is not grid related
        else:
            shape_old_grid = list(shape_old[:len(shape_old) - 1])

        # Likewise, if the last 2D is matrix_shape, then given arr is a matrix_shape tensor
        if shape_arr[len(shape_arr) - 2:] == matrix_shape:
            shape_old_grid += list(matrix_shape)
        # Otherwise, the last D is untouched
        else:
            shape_old_grid += [shape_arr[len(shape_arr) - 1]]

        arr = arr.reshape(tuple(shape_old_grid))
    # If not inferring tensor, then arr is simply reversed to shape_old
    else:
        arr = arr.reshape(shape_old)

    print('\nArray reversed from shape ' + str(shape_arr) + ' to ' + str(np.shape(arr)))
    return arr


cpdef np.ndarray rotateData(np.ndarray ndarr, double anglex=0., double angley=0., double anglez=0., tuple matrix_shape=(3, 3)):
    """
    Rotate nD array of matrices or vectors. Matrices must not be flattened.
    If infer_matrix_form, the last few D will be checked it matches provided matrix_shape. 
    The first remaining D are collapsed to 1 so that ndarr has shape (n_samples, vector size) or (n_samples, matrix_shape).
    If matrix_shape is 3D, then only the last 2D are considered for rotation. 
    E.g. Tensor basis Tij of shape (n_samples, n_bases, 3, 3) should have matrix_shape = (n_bases, 3, 3) to rotate properly.
    
    :param ndarr: nD array to rotate.
    :type ndarr: ndarray[mesh grid / n_samples x vector size / matrix_shape]
    :param anglex: Angle to rotate around x axis in degree or radian.
    :type anglex: double, optional (default=0.)
    :param angley: Angle to rotate around y axis in degree or radian.
    :type angley: double, optional (default=0.)
    :param anglez: Angle to rotate around z axis in degree or radian.
    :type anglez: double, optional (default=0.)
    :param matrix_shape: The shape to infer as matrix for rotation. 
    If matrix_shape is 3D, only the last 2D are rotated while the 1st D of matrix_shape is looped.
    E.g. to rotate tensor basis Tij of shape (n_samples / mesh grid, n_bases, 3, 3), matrix_shape should be (n_bases, 3, 3). 
    :type matrix_shape: 2/3D tuple, optional (default=(3, 3)) 
    
    :return: Rotated 2/3/4D array of vectors or matrices
    :rtype: ndarray[n_samples x vector size / matrix_shape]
    """
    cdef np.ndarray[np.float_t, ndim=2] rotij_x, rotij_y, rotij_z, qij
    cdef unsigned int i, j

    # Automatically detect wheter angles are in radian or degree
    if anglex > 2.*np.pi: anglex = anglex/180.*np.pi
    if angley > 2.*np.pi: angley = angley/180.*np.pi
    if anglez > 2.*np.pi: anglez = anglez/180.*np.pi
    # Collapse mesh grid and infer whether it's a matrix or vector
    ndarr, shape_old = collapseMeshGridFeatures(ndarr, True, matrix_shape, collapse_matrix=False)
    # Rotation matrices in x, y, z
    rotij_x, rotij_y, rotij_z = np.zeros((3, 3)), np.zeros((3, 3)), np.zeros((3, 3))
    # Qx = |1 0    0  |
    #      |0 cos -sin|
    #      |0 sin  cos|
    rotij_x[0, 0] = 1.
    rotij_x[1, 1] = rotij_x[2, 2] = cos(anglex)
    rotij_x[1, 2] = -sin(anglex)
    rotij_x[2, 1] = -rotij_x[1, 2]
    # Qy = | cos 0 sin|
    #      | 0   1 0  |
    #      |-sin 0 cos|
    rotij_y[0, 0] = rotij_y[2, 2] = cos(angley)
    rotij_y[0, 2] = sin(angley)
    rotij_y[1, 1] = 1.
    rotij_y[2, 0] = -rotij_y[0, 2]
    # Qz = |cos -sin 0|
    #      |sin  cos 0|
    #      |0    0   1|
    rotij_z[0, 0] = rotij_z[1, 1] = cos(anglez)
    rotij_z[0, 1] = -sin(anglez)
    rotij_z[1, 0] = -rotij_z[0, 1]
    rotij_z[2, 2] = 1.
    # Combined Qij
    qij = rotij_z @ (rotij_y @ rotij_x)
    # Go through each sample
    for i in range(ndarr.shape[0]):
        # For matrices, do Qij*matrix*Qij^T
        if len(np.shape(ndarr)) > 2:
            # If matrix_shape was 3D, e.g. (n_bases, 3, 3) for Tij, then loop through matrix_shape[0] too
            if len(np.shape(ndarr)) == 3:
                for j in range(ndarr.shape[1]):
                    ndarr[i, j] = qij @ (ndarr[i, j] @ qij.T)

            else:
                ndarr[i] = qij @ (ndarr[i] @ qij.T)
        # Else for vectors, do Qij*vector
        else:
            ndarr[i] = np.dot(qij, ndarr[i])

    return ndarr















