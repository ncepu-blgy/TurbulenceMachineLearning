import numpy as np
import sys
# See https://github.com/YuyangL/SOWFA-PostProcess
sys.path.append('/home/yluan/Documents/SOWFA PostProcessing/SOWFA-Postprocess')
from PostProcess_FieldData import FieldData
from Preprocess.Tensor import processReynoldsStress, getBarycentricMapData
from Preprocess.Feature import getInvariantFeatureSet
from Utility import interpolateGridData
from Preprocess.FeatureExtraction import splitTrainTestDataList
from GridSearchSetup import setupDecisionTreeGridSearchCV
import time as t
# For Python 2.7, use cpickle
try:
    import cpickle as pickle
except ModuleNotFoundError:
    import pickle

from scipy.interpolate import griddata
from numba import njit, prange
from Utilities import timer
from sklearn.tree import plot_tree, DecisionTreeRegressor
import matplotlib.pyplot as plt
from PlottingTool import BaseFigure
from scipy import ndimage
from matplotlib.path import Path
from matplotlib.patches import PathPatch
from copy import copy
from joblib import load, dump
from sklearn.ensemble import RandomForestRegressor


"""
User Inputs, Anything Can Be Changed Here
"""
# Name of the flow case in both RANS and LES
rans_case_name = 'RANS_Re10595'  # str
les_case_name = 'LES_Breuer/Re_10595'  # str
# LES data name to read
les_data_name = 'Hill_Re_10595_Breuer.csv'  # str
# Absolute directory of this flow case
caseDir = '/media/yluan/DNS/PeriodicHill'  # str
# Which time to extract input and output for ML
time = '5000'  # str/float/int or 'last'
# Interpolation method when interpolating mesh grids
interp_method = "nearest"  # "nearest", "linear", "cubic"
# Eddy-viscosity coefficient to convert omega to epsilon via
# epsilon = Cmu*k*omega
cmu = 0.09  # float
# Whether process field data, invariants, features from scratch,
# or use raw field pickle data and process invariants and features
# or use raw field and invariants pickle data and process features
process_raw_field, process_invariants = False, False  # bool
# The following is only relevant if processInvariants is True
if process_invariants:
    # Absolute cap value for Sij and Rij; tensor basis Tij
    cap_sij_rij, cap_tij = 1e9, 1e9  # float/int


"""
Machine Learning Settings
"""
# Whether to calculate features or directly read from pickle data
calculate_features = False  # bool
# Whether to split train and test data or directly read from pickle data
split_train_test_data = False  # bool
# Feature set number
fs = 'grad(TKE)_grad(p)'  # '1', '12', '123'
# Whether to train the model or directly load it from saved joblib file;
# and whether to save estimator after training
train_model, save_estimator = True, True  # bool
# Name of the ML estimator
estimator_name = 'tbrf'  # "TBDT" or "tbdt" or "TBRF" or "tbrf"
scaler = None  # "robust", "standard" or None
# Whether to presort X for every feature before finding the best split at each node
presort = True  # bool
# Maximum number of features to consider for best split
max_features = 0.8#(0.8, 1.)  # list/tuple(int / float 0-1) or int / float 0-1
# Minimum number of samples at leaf
min_samples_leaf = 2#(4, 16)  # list/tuple(int / float 0-1) or int / float 0-1
# Minimum number of samples to perform a split
min_samples_split = 4#(8, 64)  # list/tuple(int / float 0-1) or int / float 0-1
# Max depth of the tree to prevent overfitting
max_depth = 50  # int
# L2 regularization fraction to penalize large optimal 10 g found through LS fit of min_g(bij - Tij*g)
alpha_g_fit = 0#(0., 0.001)  # list/tuple(float) or float
# L2 regularization coefficient to penalize large optimal 10 g during best split finder
alpha_g_split = 0#(0., 0.001)  # list/tuple(float) or float
# Best split finding scheme to speed up the process of locating the best split in samples of each node
split_finder = "1000"  # "brute", "brent", "1000", "auto"
# Cap of optimal g magnitude after LS fit
g_cap = None  # int or float or None
if estimator_name in ("TBRF", "tbrf"):
    n_estimators = 10  # int
    oob_score = True  # bool

# Seed value for reproducibility
seed = 123
# For debugging, verbose on bij reconstruction from Tij and g;
# and/or verbose on "brent"/"brute"/"1000"/"auto" split finding scheme
tb_verbose, split_verbose = False, False  # bool; bool
# Fraction of data for testing
test_fraction = 0.2  # float [0-1]
# Whether verbose on GSCV. 0 means off
gscv_verbose = 1  # int
# Number of n-fold cross validation
cv = 5  # int


"""
Plot Settings
"""
# Whether to plot velocity magnitude to verify readFieldData()
plot_u = False  # bool
# When plotting, the mesh has to be uniform by interpolation, specify target size
uniform_mesh_size = 1e6  # int
# Save anything when possible
save_fields = True  # bool
# Save figures and show figures
save_fig, show = True, False  # bool; bool
if save_fig:
    # Figure extension and DPI
    ext, dpi = 'png', 600  # str; int


"""
Process User Inputs, No Need to Change
"""
# Average fields of interest for reading and processing
fields = ('U', 'k', 'p', 'omega',
          'grad_U', 'grad_k', 'grad_p')
# if fs == "grad(TKE)_grad(p)":
#     fields = ('U', 'k', 'p', 'omega',
#               'grad_U', 'grad_k', 'grad_p')
# elif fs == "grad(TKE)":
#     fields = ('k', 'omega',
#               'grad_U', 'grad_k')
# elif fs == "grad(p)":
#     fields = ('U', 'k', 'p', 'omega',
#               'grad_U', 'grad_p')
# else:
#     fields = ('k', 'omega',
#               'grad_U')

# Ensemble name of fields useful for Machine Learning
ml_field_ensemble_name = 'ML_Fields_' + rans_case_name
# Initialize case object
case = FieldData(caseName=rans_case_name, caseDir=caseDir, times=time, fields=fields, save=save_fields)
if estimator_name == "tbdt":
    estimator_name = "TBDT"
elif estimator_name == "tbrf":
    estimator_name = "TBRF"



"""
Read and Process Raw Field Data
"""
if process_raw_field:
    # Read raw field data specified in fields
    field_data = case.readFieldData()
    # Assign fields to their corresponding variable
    grad_u, u = field_data['grad_U'], field_data['U']
    grad_k, k = field_data['grad_k'], field_data['k']
    grad_p, p = field_data['grad_p'], field_data['p']
    omega = field_data['omega']
    # Get turbulent energy dissipation rate
    epsilon = cmu*k*omega
    # Convert 1D array to 2D so that I can hstack them to 1 array ensemble, n_points x 1
    k, epsilon = k.reshape((-1, 1)), epsilon.reshape((-1, 1))
    # Assemble all useful fields for Machine Learning
    ml_field_ensemble = np.hstack((grad_k, k, epsilon, grad_u, u, grad_p))
    print('\nField variables identified')
    # Read cell center coordinates of the whole domain, nCell x 0
    ccx, ccy, ccz, cc = case.readCellCenterCoordinates()
    # Save all whole fields and cell centers
    case.savePickleData(time, ml_field_ensemble, fileNames=ml_field_ensemble_name)
    case.savePickleData(time, cc, fileNames='cc')
# Else if directly read pickle data
else:
    # Load rotated and/or confined field data useful for Machine Learning
    ml_field_ensemble = case.readPickleData(time, ml_field_ensemble_name)
    grad_k, k = ml_field_ensemble[:, :3], ml_field_ensemble[:, 3]
    epsilon = ml_field_ensemble[:, 4]
    grad_u, u = ml_field_ensemble[:, 5:14], ml_field_ensemble[:, 14:17]
    grad_p = ml_field_ensemble[:, 17:20]
    # Load confined cell centers too
    cc = case.readPickleData(time, 'cc')

if plot_u:
    umag = np.sqrt(u[:, 0]**2 + u[:, 1]**2 + u[:, 2]**2)
    ccx_mesh_u, ccy_mesh_u, _, umag_mesh = interpolateGridData(cc[:, 0], cc[:, 1], umag, mesh_target=uniform_mesh_size, fill_val=0)
    umag_mesh = ndimage.rotate(umag_mesh, 90)
    plt.figure('U magnitude', constrained_layout=True)
    plt.imshow(umag_mesh, origin='upper', aspect='equal', cmap='inferno')


"""
Calculate Field Invariants
"""
if process_invariants:
    # Step 1: strain rate and rotation rate tensor Sij and Rij
    sij, rij = case.getStrainAndRotationRateTensorField(grad_u, tke=k, eps=epsilon, cap = cap_sij_rij)
    # Step 2: 10 invariant bases TB
    tb = case.getInvariantBasesField(sij, rij, quadratic_only = False, is_scale = True)
    # Since TB is n_points x 10 x 3 x 3, reshape it to n_points x 10 x 9
    tb = tb.reshape((tb.shape[0], tb.shape[1], 9))
    # Step 3: anisotropy tensor bij from LES of Breuer csv data
    # 0: x; 1: y; 6: u'u'; 7: v'v'; 8: w'w'; 9: u'v'
    les_data = np.genfromtxt(caseDir + '/' + les_case_name + '/' + les_data_name,
                             delimiter=',', skip_header=1, usecols=(0, 1, 6, 7, 8, 9))
    # Assign each column to corresponding field variable
    # LES cell centers with z being 0
    cc_les = np.zeros((len(les_data), 3))
    cc_les[:, :2] = les_data[:, :2]
    # LES Reynolds stress has 0 u'w' and v'w' 
    uu_prime2_les = np.zeros((len(cc_les), 6))
    # u'u', u'v'
    uu_prime2_les[:, 0], uu_prime2_les[:, 1] = les_data[:, 2], les_data[:, 5]
    # v'v'
    uu_prime2_les[:, 3] = les_data[:, 3]
    # w'w'
    uu_prime2_les[:, 5] = les_data[:, 4]
    # Get LES anisotropy tensor field bij
    bij6_les_all = case.getAnisotropyTensorField(uu_prime2_les)
    # Interpolate LES bij to the same grid of RANS
    bij6_les = np.empty((len(cc), 6))
    # Go through every bij component and interpolate
    print("\nInterpolating LES bij to RANS grid...")
    for i in range(6):
        bij6_les[:, i] = griddata(cc_les[:, :2], bij6_les_all[:, i], cc[:, :2], method=interp_method)

    # Expand bij from 6 components to its full 9 components form
    bij_les = np.zeros((len(cc), 9))
    # b11, b12, b13
    bij_les[:, :3] = bij6_les[:, :3]
    # b21, b22, b23
    bij_les[:, 3], bij_les[:, 4:6] = bij6_les[:, 1], bij6_les[:, 3:5]
    # b31, b32, b33
    bij_les[:, 6], bij_les[:, 7:] = bij6_les[:, 2], bij6_les[:, 4:]
    # If save_fields, save the processed RANS invariants and LES bij (interpolated to same grid of RANS)
    if save_fields:
        case.savePickleData(time, sij, fileNames = ('Sij'))
        case.savePickleData(time, rij, fileNames = ('Rij'))
        case.savePickleData(time, tb, fileNames = ('Tij'))
        case.savePickleData(time, bij_les, fileNames = ('bij_LES'))

# Else if read RANS invariants and LES bij data from pickle
else:
    invariants = case.readPickleData(time, fileNames = ('Sij',
                                                        'Rij',
                                                        'Tij',
                                                        'bij_LES'))
    sij = invariants['Sij']
    rij = invariants['Rij']
    tb = invariants['Tij']
    bij_les = invariants['bij_LES']
    cc = case.readPickleData(time, fileNames='cc')


"""
Calculate Feature Sets
"""
if calculate_features:
    # Feature set 1
    # fs1 = getFeatureSet1(sij, rij)
    if fs == 'grad(TKE)':
        fs_data, labels = getInvariantFeatureSet(sij, rij, grad_k, k=k, eps=epsilon)
    elif fs == 'grad(p)':
        fs_data, labels = getInvariantFeatureSet(sij, rij, grad_p=grad_p, u=u, grad_u=grad_u)
    elif fs == 'grad(TKE)_grad(p)':
        fs_data, labels = getInvariantFeatureSet(sij, rij, grad_k=grad_k, grad_p=grad_p, k=k, eps=epsilon, u=u, grad_u=grad_u)
    # If only feature set 1 used for ML input, then do train test data split here
    if save_fields:
        case.savePickleData(time, fs_data, fileNames = ('FS_' + fs))

# Else, directly read feature set data
else:
    fs_data = case.readPickleData(time, fileNames = ('FS_' + fs))


"""
Machine Learning Train, Test Data Preparation
"""
@timer
@njit(parallel=True)
def _transposeTensorBasis(tb):
    tb_transpose = np.empty((len(tb), tb.shape[2], tb.shape[1]))
    for i in prange(len(tb)):
        tb_transpose[i] = tb[i].T

    return tb_transpose

# If split train and test data instead of directly read from pickle data
if split_train_test_data:
    # Transpose Tij so that it's n_samples x 9 components x 10 bases
    tb = _transposeTensorBasis(tb)
    # X is RANS invariant features
    x = fs_data
    # y is LES bij interpolated to RANS grid
    y = bij_les
    # Train-test data split, incl. cell centers and Tij
    list_data_train, list_data_test = splitTrainTestDataList([cc, x, y, tb], test_fraction=test_fraction, seed=seed)
    if save_fields:
        # Extra tuple treatment to list_data_t* that's already a tuple since savePickleData thinks tuple means multiple files
        case.savePickleData(time, (list_data_train,), 'list_data_train_seed' + str(seed))
        case.savePickleData(time, (list_data_test,), 'list_data_test_seed' + str(seed))

# Else if directly read train and test data from pickle data
else:
    list_data_train = case.readPickleData(time, 'list_data_train_seed' + str(seed))
    list_data_test = case.readPickleData(time, 'list_data_test_seed' + str(seed))

cc_train, cc_test = list_data_train[0], list_data_test[0]
ccx_train, ccy_train, ccz_train = cc_train[:, 0], cc_train[:, 1], cc_train[:, 2]
ccx_test, ccy_test, ccz_test = cc_test[:, 0], cc_test[:, 1], cc_test[:, 2]
x_train, y_train, tb_train = list_data_train[1:4]
x_test, y_test, tb_test = list_data_test[1:4]


"""
Machine Learning Training
"""
if train_model:
    if estimator_name == 'TBDT':
        regressor, tune_params = setupDecisionTreeGridSearchCV(max_features=max_features, min_samples_split=min_samples_split, min_samples_leaf=min_samples_leaf,
                                                               alpha_g_fit=alpha_g_fit, alpha_g_split=alpha_g_split,
                                                  presort=presort, split_finder=split_finder,
                                                  tb_verbose=tb_verbose, split_verbose=split_verbose, scaler=scaler, rand_state=seed, gscv_verbose=gscv_verbose,
                                                               cv=cv, max_depth=max_depth,
                                                               g_cap=g_cap)
    elif estimator_name == 'TBRF':
        regressor = RandomForestRegressor(n_estimators=n_estimators, max_depth=max_depth, min_samples_split=min_samples_split,
                                          min_samples_leaf=min_samples_leaf, max_features=max_features,
                                          oob_score=oob_score, n_jobs=-1,
                                          random_state=seed, verbose=gscv_verbose,
                                          tb_verbose=tb_verbose, split_finder=split_finder,
                                          split_verbose=split_verbose, alpha_g_fit=alpha_g_fit,
                                          alpha_g_split=alpha_g_split, g_cap=g_cap)

    t0 = t.time()
    # regressor = DecisionTreeRegressor(presort=presort, max_depth=max_depth, tb_verbose=tb_verbose, min_samples_leaf=min_samples_leaf,
    #                                   min_samples_split=min_samples_split,
    #                                   split_finder=split_finder, split_verbose=split_verbose, max_features=max_features,
    #                                   alpha_g_fit=alpha_g_fit,
    #                                   alpha_g_split=alpha_g_split)
    regressor.fit(x_train, y_train, tb=tb_train)
    t1 = t.time()
    print('\nFinished DecisionTreeRegressor in {:.4f} s'.format(t1 - t0))
    if save_estimator:
        dump(regressor, case.resultPaths[time] + estimator_name + '.joblib')

else:
    regressor = load(case.resultPaths[time] + estimator_name + '.joblib')

score_test = regressor.score(x_test, y_test, tb_test)
score_train = regressor.score(x_train, y_train, tb_train)

# plt.figure(num="DBRT", figsize=(16, 10))
# try:
#     plot = plot_tree(regressor.best_estimator_, fontsize=6, max_depth=5, filled=True, rounded=True, proportion=True, impurity=False)
# except AttributeError:
#     plot = plot_tree(regressor, fontsize=6, max_depth=5, filled=True, rounded=True, proportion=True, impurity=False)

t0 = t.time()
y_pred_test = regressor.predict(x_test, tb=tb_test)
y_pred_train = regressor.predict(x_train, tb=tb_train)
t1 = t.time()
print('\nFinished bij prediction in {:.4f} s'.format(t1 - t0))



print('\n\nLoading regressor... \n')
reg2 = load(case.resultPaths[time] + estimator_name + '.joblib')
score_test2 = reg2.score(x_test, y_test, tb_test)
score_train2 = reg2.score(x_train, y_train, tb_train)

t0 = t.time()
y_pred_test2 = reg2.predict(x_test, tb=tb_test)
y_pred_train2 = reg2.predict(x_train, tb=tb_train)
t1 = t.time()
print('\nFinished bij prediction in {:.4f} s'.format(t1 - t0))




"""
Postprocess Machine Learning Predictions
"""
t0 = t.time()
_, eigval_test, _ = processReynoldsStress(y_test, make_anisotropic=False, realization_iter=0)
_, eigval_train, _ = processReynoldsStress(y_train, make_anisotropic=False, realization_iter=0)
_, eigval_pred_test, _ = processReynoldsStress(y_pred_test, make_anisotropic=False, realization_iter=5)
_, eigval_pred_train, _ = processReynoldsStress(y_pred_train, make_anisotropic=False, realization_iter=5)
t1 = t.time()
print('\nFinished processing Reynolds stress in {:.4f} s'.format(t1 - t0))

t0 = t.time()
xy_bary_test, rgb_bary_test = getBarycentricMapData(eigval_test)
xy_bary_train, rgb_bary_train = getBarycentricMapData(eigval_train)
xy_bary_pred_test, rgb_bary_pred_test = getBarycentricMapData(eigval_pred_test)
xy_bary_pred_train, rgb_bary_pred_train = getBarycentricMapData(eigval_pred_train)
t1 = t.time()
print('\nFinished getting Barycentric map data in {:.4f} s'.format(t1 - t0))

t0 = t.time()
ccx_test_mesh, ccy_test_mesh, _, rgb_bary_test_mesh = interpolateGridData(ccx_test, ccy_test, rgb_bary_test, mesh_target=uniform_mesh_size, interp=interp_method, fill_val=0.3)
ccx_train_mesh, ccy_train_mesh, _, rgb_bary_train_mesh = interpolateGridData(ccx_train, ccy_train, rgb_bary_train, mesh_target=uniform_mesh_size, interp=interp_method, fill_val=0.3)
_, _, _, rgb_bary_pred_test_mesh = interpolateGridData(ccx_test, ccy_test, rgb_bary_pred_test, mesh_target=uniform_mesh_size, interp=interp_method, fill_val=0.3)
_, _, _, rgb_bary_pred_train_mesh = interpolateGridData(ccx_train, ccy_train, rgb_bary_pred_train, mesh_target=uniform_mesh_size, interp=interp_method, fill_val=0.3)
t1 = t.time()
print('\nFinished interpolating mesh data in {:.4f} s'.format(t1 - t0))


"""
Plotting
"""
rgb_bary_test_mesh = ndimage.rotate(rgb_bary_test_mesh, 90)
rgb_bary_train_mesh = ndimage.rotate(rgb_bary_train_mesh, 90)
rgb_bary_pred_test_mesh = ndimage.rotate(rgb_bary_pred_test_mesh, 90)
rgb_bary_pred_train_mesh = ndimage.rotate(rgb_bary_pred_train_mesh, 90)
xlabel, ylabel = (r'$x$ [m]', r'$y$ [m]')
geometry = np.genfromtxt(case.resultPaths[time] + "geometry.csv", delimiter=",")[:, :2]
figname = 'barycentric_periodichill_test_seed' + str(seed)
bary_map = BaseFigure((None,), (None,), name=figname, xLabel=xlabel,
                      yLabel=ylabel, save=save_fig, show=show,
                      figDir=case.resultPaths[time])
path = Path(geometry)
patch = PathPatch(path, linewidth=0., facecolor=bary_map.gray)
# patch is considered "a single artist" so have to make copy to use more than once
patch2, patch3, patch4 = copy(patch), copy(patch), copy(patch)
bary_map.initializeFigure()
extent = (ccx_test.min(), ccx_test.max(), ccy_test.min(), ccy_test.max())
bary_map.axes[0].imshow(rgb_bary_test_mesh, origin='upper', aspect='equal', extent=extent)
bary_map.axes[0].set_xlabel(bary_map.xLabel)
bary_map.axes[0].set_ylabel(bary_map.yLabel)
bary_map.axes[0].add_patch(patch)
if save_fig:
    plt.savefig(case.resultPaths[time] + figname + '.' + ext, dpi=dpi)

bary_map.name = 'barycentric_periodichill_pred_test_seed' + str(seed)
bary_map.initializeFigure()
bary_map.axes[0].imshow(rgb_bary_pred_test_mesh, origin='upper', aspect='equal', extent=extent, interpolation="bicubic")
bary_map.axes[0].set_xlabel(bary_map.xLabel)
bary_map.axes[0].set_ylabel(bary_map.yLabel)
bary_map.axes[0].add_patch(patch2)
if save_fig:
    plt.savefig(case.resultPaths[time] + bary_map.name + '.' + ext, dpi=dpi)

bary_map.name = 'barycentric_periodichill_train_seed' + str(seed)
bary_map.initializeFigure()
extent = (ccx_train.min(), ccx_train.max(), ccy_train.min(), ccy_train.max())
bary_map.axes[0].imshow(rgb_bary_train_mesh, origin='upper', aspect='equal', extent=extent)
bary_map.axes[0].set_xlabel(bary_map.xLabel)
bary_map.axes[0].set_ylabel(bary_map.yLabel)
bary_map.axes[0].add_patch(patch3)
if save_fig:
    plt.savefig(case.resultPaths[time] + bary_map.name + '.' + ext, dpi=dpi)

bary_map.name = 'barycentric_periodichill_pred_train_seed' + str(seed)
bary_map.initializeFigure()
bary_map.axes[0].imshow(rgb_bary_pred_train_mesh, origin='upper', aspect='equal', extent=extent)
bary_map.axes[0].set_xlabel(bary_map.xLabel)
bary_map.axes[0].set_ylabel(bary_map.yLabel)
bary_map.axes[0].add_patch(patch4)
if save_fig:
    plt.savefig(case.resultPaths[time] + bary_map.name + '.' + ext, dpi=dpi)




