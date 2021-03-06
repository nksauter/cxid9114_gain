#!/usr/bin/env python
# -*- mode: python; coding: utf-8; indent-tabs-mode: nil; python-indent: 2 -*-
#
from __future__ import division

import numpy as np
from scipy import constants as sci_cons
from six.moves import range
import math, copy
from scitbx import matrix
from scitbx.matrix import col
from dials.array_family import flex
from dials.algorithms.indexing.indexer import optimise_basis_vectors
from dials.algorithms.indexing.indexer import \
     is_approximate_integer_multiple
from dxtbx.model.experiment_list import Experiment, ExperimentList
from dials.algorithms.shoebox import MaskCode
from dials.algorithms.indexing.stills_indexer import stills_indexer

from memory_profiler import profile

help_message = '''
This program indexes images with diffraction spots at two distinct wavelengths.
The indexing algorithm derives from real space grid search. The 2-D grid search
functions identically to real space grid search with the exception of an new
input functional that incorporates both wavelengths.

Once a candidate orientation is determined the basis is used to assign spots
to the appropriate wavelength based on the difference between the fractional
hkl and its corresponding integer hkl. Next, the candidate hkls are searched
to discover if there are multiple assignments of the same hkl to the same
wavelength (experiment). If such a case is found the magnitude of the
corresponding reciprocal lattice vectors are calculated and the shortest is
assigned to the orignal wavelength and the longer is moved to the other wavelength.

The first indexing results are passed with two experiment ids, one for each
wavelength, to the refiner and the orientation matrix is refined for specified
number of macrocycles.

Plotting can be uncommented in order to compare the simulated reflections with
the indexed reflections at each stage of refinement.


Examples::
to be added when this indexing algorithm is part of the phil file for indexing
'''

N_UNIQUE_V = 60 # this is the number of directions used to search for indexing solutions (usually set to 30 from Guildea paper)
EV_CONV_FACTOR = round( 1e10* sci_cons.h * sci_cons.c / sci_cons.electron_volt, 5)

def index_reflections_detail(debug, experiments,
                             reflections,
                             detector,
                             reciprocal_lattice_points1,
                             reciprocal_lattice_points2,
                             d_min=None,
                             tolerance=0.3,
                             verbosity=0):
  ''' overwrites base class index_reflections function and assigns spots to
     their corresponding experiment (wavelength)'''

  print("\n\n special indexing \n\n")
  # initialize each reflections miller index to 0,0,0
  reflections['miller_index'] = flex.miller_index(len(reflections), (0,0,0))

  # for two wavelengths
  assert len(experiments) == 3
  low_energy = 0   # 0th experiment is low-energy
  high_energy = 1  # 1st experiment is high-energ
  avg_energy = 2  # 2nd experiment is average energy (for spot overlaps)

  # code to check input orientation matrix
  # get predicted reflections based on basis vectors
  pred = False
  if pred ==True:
    experiments[0].crystal._ML_half_mosaicity_deg = .2
    experiments[0].crystal._ML_domain_size_ang = 1000
    predicted = flex.reflection_table.from_predictions_multi(experiments[0:2])
    predicted.as_pickle('test')

  inside_resolution_limit = flex.bool(len(reflections), True)
  if d_min is not None:
    d_spacings = 1/reflections['rlp'].norms()
    inside_resolution_limit &= (d_spacings > d_min)

  # boolean array, all yet-to-be spots that are bound by the resolution
  sel = inside_resolution_limit & (reflections['id'] == -1)
  # array of indices of the reflections
  isel = sel.iselection()
# I believe .select( isel) is same as .select( sel)
  rlps0 = reciprocal_lattice_points1.select(isel)  # low-energy beam lp vectors calculated in two_color_grid_search
  rlps1 = reciprocal_lattice_points2.select(isel)  # high-energy beam lps!
  refs = reflections.select(isel)


  rlps = (rlps0, rlps1)  # put em in a tuple ?
  rlp_norms = []
  hkl_ints = []
  norms = []
  diffs = []
  c1 = experiments.crystals()[0]
  assert( len(experiments.crystals()) == 1 )  # 3 beams but only 1 crystal!
  A = matrix.sqr( experiments.crystals()[0].get_A())
  A_inv = A.inverse()

  # confusing variable names, but for each set of R.L.P.s.
  # (one for the high and one for the low energy beam)
  # find the distance to the nearest integer hkl
  for rlp in range(len(rlps)):
    hkl_float = tuple(A_inv) * rlps[rlp]
    hkl_int = hkl_float.iround()
    differences = hkl_float - hkl_int.as_vec3_double()
    diffs.append(differences)
    norms.append(differences.norms())
    hkl_ints.append(hkl_int)

  n_rejects = 0
  for i_hkl in range(hkl_int.size()):
    n = flex.double([norms[j][i_hkl]
                     for j in range(len(rlps))])
    potential_hkls = [hkl_ints[j][i_hkl]
                      for j in range(len(rlps))]
    potential_rlps = [rlps[j][i_hkl]
                      for j in range(len(rlps))]
    if norms[0][i_hkl]>norms[1][i_hkl]:
      i_best_lattice = high_energy
      i_best_rlp = high_energy
    elif norms[0][i_hkl]<norms[1][i_hkl]:
      i_best_lattice = low_energy
      i_best_rlp = low_energy
    else:
      i_best_lattice = flex.min_index(n)
      i_best_rlp = flex.min_index(n)
    if n[i_best_lattice] > tolerance:
      n_rejects += 1
      continue
    miller_index = potential_hkls[i_best_lattice]
    reciprocal_lattice_points = potential_rlps[i_best_rlp]
    i_ref = isel[i_hkl]
    reflections['miller_index'][i_ref] = miller_index
    reflections['id'][i_ref] = i_best_lattice
    reflections['rlp'][i_ref] = reciprocal_lattice_points

  # if more than one spot can be assigned the same miller index then choose
  # the closest one
  miller_indices = reflections['miller_index'].select(isel)
  rlp_norms = reflections['rlp'].select(isel).norms()
  same=0
  for i_hkl, hkl in enumerate(miller_indices):
    if hkl == (0,0,0): continue
    iselection = (miller_indices == hkl).iselection()
    if len(iselection) > 1:
      for i in iselection:
        for j in iselection:
          if j <= i: continue
          crystal_i = reflections['id'][isel[i]]
          crystal_j = reflections['id'][isel[j]]
          if crystal_i != crystal_j:
            continue
          elif (crystal_i == -1 or crystal_j ==-1) or (crystal_i == -2 or crystal_j == -2):
            continue
          elif crystal_i ==2 or crystal_j ==2:
            continue
            #print hkl_ints[crystal_i][i], hkl_ints[crystal_j][j], crystal_i
          assert hkl_ints[crystal_j][j] == hkl_ints[crystal_i][i]
          same +=1
          if rlp_norms[i] < rlp_norms[j]:
            reflections['id'][isel[i]] = high_energy
            reflections['id'][isel[j]] = low_energy
          elif rlp_norms[j] < rlp_norms[i]:
            reflections['id'][isel[j]] = high_energy
            reflections['id'][isel[i]] = low_energy

  #calculate Bragg angles
  s0 = col(experiments[2].beam.get_s0())
  lambda_0 = experiments[0].beam.get_wavelength()
  lambda_1 = experiments[1].beam.get_wavelength()
  det_dist = experiments[0].detector[0].get_distance()
  px_size_mm = experiments[0].detector[0].get_pixel_size()[0]
  spot_px_coords=reflections['xyzobs.px.value'].select(isel)
  px_x,px_y,px_z = spot_px_coords.parts()
  res  = []
  for i in range(len(spot_px_coords)):
    res.append(detector[0].get_resolution_at_pixel(s0, (px_x[i], px_y[i])))
  # predicted spot distance  based on the resultion of the observed spot at either wavelength 1 or 2
  theta_1a = [math.asin(lambda_0/(2*res[i])) for i in range(len(res))]
  theta_2a = [math.asin(lambda_1/(2*res[i])) for i in range(len(res))]
  px_dist = [(math.tan(2*theta_1a[i])*det_dist-math.tan(2*theta_2a[i])*det_dist)/px_size_mm for i in range(len(spot_px_coords))]
  # first calculate distance from stop centroid to farthest valid pixel (determine max spot radius)
  # coords of farthest valid pixel
  # if the predicted spot distance at either wavelength is less than 2x distance described above than the spot is considered "overlapped" and assigned to experiment 2 at average wavelength

  valid = MaskCode.Valid | MaskCode.Foreground

  for i in range(len(refs)):
    if reflections['miller_index'][isel[i]]==(0,0,0): continue
    sb = reflections['shoebox'][isel[i]]
    bbox = sb.bbox
    mask = sb.mask
    centroid = col(reflections['xyzobs.px.value'][isel[i]][0:2])
    x1, x2, y1, y2, z1, z2 = bbox

    longest = 0
    for y in range(y1, y2):
      for x in range(x1, x2):
        if mask[z1,y-y1,x-x1] != valid:
          continue
        v = col([x,y])
        dist = (centroid -v).length()
        if dist > longest:
          longest = dist
    #print "Miller Index", reflections['miller_index'][i], "longest", longest,"predicted distance", px_dist_1[i]
    if 2*longest > px_dist[i]:
      avg_rlp0 = reflections['rlp'][isel[i]][0]*experiments[reflections['id'][isel[i]]].beam.get_wavelength()/experiments[2].beam.get_wavelength()
      avg_rlp1 = reflections['rlp'][isel[i]][1]*experiments[reflections['id'][isel[i]]].beam.get_wavelength()/experiments[2].beam.get_wavelength()
      avg_rlp2 = reflections['rlp'][isel[i]][2]*experiments[reflections['id'][isel[i]]].beam.get_wavelength()/experiments[2].beam.get_wavelength()
      reflections['id'][isel[i]] = avg_energy
      reflections['rlp'][isel[i]] = (avg_rlp0, avg_rlp1, avg_rlp2)

  # check for repeated hkl in experiment 2, and if experiment 2 has same hkl as experiment 0 or 1 the spot with the largest variance is assigned to experiment -2 and the remaining spot is assigned to experiment 2

  for i_hkl, hkl in enumerate(miller_indices):
    if hkl == (0,0,0): continue
    iselection = (miller_indices == hkl).iselection()
    if len(iselection) > 1:
      for i in iselection:
        for j in iselection:
          if j <= i: continue
          crystal_i = reflections['id'][isel[i]]
          crystal_j = reflections['id'][isel[j]]
          if (crystal_i == -1 or crystal_j ==-1) or (crystal_i == -2 or crystal_j == -2):
            continue
          # control to only filter for experient 2; duplicate miller indices in 0 and 1 are resolved above
          if (crystal_i == 1 and crystal_j == 0) or (crystal_i == 0 and crystal_j ==1):
            continue

          if (crystal_i ==2 or crystal_j ==2) and (reflections['xyzobs.px.variance'][isel[i]]<reflections['xyzobs.px.variance'][isel[j]]):
              reflections['id'][isel[j]] = -2
              reflections['id'][isel[i]] = avg_energy
          elif (crystal_i ==2 or crystal_j ==2) and (reflections['xyzobs.px.variance'][isel[i]]>reflections['xyzobs.px.variance'][isel[j]]):
              reflections['id'][isel[i]] = -2
              reflections['id'][isel[j]] = avg_energy
          if (crystal_i ==2 and crystal_j ==2) and (reflections['xyzobs.px.variance'][isel[i]]<reflections['xyzobs.px.variance'][isel[j]]):
              reflections['id'][isel[j]] = -2
              reflections['id'][isel[i]] = avg_energy
          elif (crystal_i ==2 and crystal_j ==2) and (reflections['xyzobs.px.variance'][isel[i]]>reflections['xyzobs.px.variance'][isel[j]]):
              reflections['id'][isel[i]] = -2
              reflections['id'][isel[j]] = avg_energy

  # check that each experiment list does not contain duplicate miller indices
  exp_0 = reflections.select(reflections['id']==0)
  exp_1 = reflections.select(reflections['id']==1)
  exp_2 = reflections.select(reflections['id']==2)
  #assert len(exp_0['miller_index'])==len(set(exp_0['miller_index']))
  #assert len(exp_1['miller_index'])==len(set(exp_1['miller_index']))
  #assert len(exp_2['miller_index'])==len(set(exp_2['miller_index']))


class indexer_two_color(stills_indexer):
  ''' class to calculate orientation matrix for 2 color diffraction images '''

  def __init__(self, reflections, imagesets, params):
    assert len(imagesets) == 1

    beam = imagesets[0].get_beam()
    beam1 = copy.deepcopy(beam)
    beam2 = copy.deepcopy(beam)
    beam3 = copy.deepcopy(beam)
    wavelength1 = EV_CONV_FACTOR/params.indexing.two_color.low_energy
    wavelength2 = EV_CONV_FACTOR/params.indexing.two_color.high_energy
    wavelength3 = EV_CONV_FACTOR/params.indexing.two_color.avg_energy
    beam1.set_wavelength(wavelength1)
    beam2.set_wavelength(wavelength2)
    beam3.set_wavelength(wavelength3)
    self.beams = [beam1, beam2, beam3]
    self.debug = params.indexing.two_color.debug
    super(indexer_two_color, self).__init__(reflections, imagesets, params)

  def index(self):
    super(indexer_two_color, self).index()

    experiments2 = ExperimentList()
    indexed2 = flex.reflection_table()
    for e_number in range(len(self.refined_experiments)):
      experiments2.append(self.refined_experiments[e_number])
      e_selection = flex.bool( [r['id']==e_number or r['id']==2 for r in self.refined_reflections])
      e_indexed = self.refined_reflections.select(e_selection)
      e_indexed['id'] = flex.int(len(e_indexed), e_number) # renumber all
      indexed2.extend(e_indexed)
      if e_number >=1: break
    self.refined_experiments = experiments2
    self.refined_reflections = indexed2

  def index_reflections(self,experiments,reflections,verbosity=0):
    '''
    if there are two or more experiments calls overloaded index_reflections
    This method should do everything that indexer_base.index_reflections does..
    which appears to be setting some kind of reflectio flags
    '''
    assert len(experiments) > 1
    assert len(self.imagesets) == 1

    #if len(experiments) == 1:
    #  self.index_reflections(self, experiments, reflections)
    #else:
    params_simple = self.params.index_assignment.simple
    index_reflections_detail(self.debug, experiments, reflections,
                             self.imagesets[0].get_detector(),
                             self.reciprocal_lattice_points1,
                             self.reciprocal_lattice_points2,
                             self.d_min,
                             tolerance=params_simple.hkl_tolerance,
                             verbosity=verbosity)

    for i in range(len(reflections)):
      print("BLILILILIT")
      if reflections['id'][i] == -1:
        reflections['id'][i] = 0

    reflections.set_flags(
      reflections['miller_index'] != (0,0,0), reflections.flags.indexed)

  def experiment_list_for_crystal(self, crystal):
    experiments = ExperimentList()
    for beam in self.beams:
      for imageset in self.imagesets:
        experiments.append(Experiment(imageset=imageset,
                                      beam=beam,
                                      detector=imageset.get_detector(),
                                      goniometer=imageset.get_goniometer(),
                                      scan=imageset.get_scan(),
                                      crystal=crystal))

    return experiments

  def find_lattices(self):
    '''assigns the crystal model and the beam(s) to the experiment list'''
    print("\n\nFINDING LATTTTSSSSSS\n")
    self.two_color_grid_search()
    crystal_models = self.candidate_crystal_models
    assert len(crystal_models) == 1
    # only return the experiments 0 and 1
    return self.experiment_list_for_crystal(crystal_models[0])

  def two_color_grid_search(self):
    '''creates candidate reciprocal lattice points based on two beams and performs
    2-D grid search based on maximizing the functional using N_UNIQUE_V candidate
    vectors (N_UNIQUE_V is usually 30 from Guildea paper)'''

    assert len(self.imagesets) == 1
    detector = self.imagesets[0].get_detector()

    mm_spot_pos = self.map_spots_pixel_to_mm_rad(self.reflections,detector,scan=None)

    self.map_centroids_to_reciprocal_space(mm_spot_pos,detector,self.beams[0],
                                           goniometer=None)
    self.reciprocal_lattice_points1 = mm_spot_pos['rlp'].select(
          (self.reflections['id'] == -1))

    rlps1 = mm_spot_pos['rlp'].select(
          (self.reflections['id'] == -1))

    self.map_centroids_to_reciprocal_space(mm_spot_pos,detector,self.beams[1],
                                           goniometer=None)
    self.reciprocal_lattice_points2 = mm_spot_pos['rlp'].select(
          (self.reflections['id'] == -1))
    # assert len(self.beams) == 3
    rlps2 = mm_spot_pos['rlp'].select(
          (self.reflections['id'] == -1))

    self.reciprocal_lattice_points=rlps1.concatenate(rlps2)

    #self.map_centroids_to_reciprocal_space(mm_spot_pos,detector,self.beams[2],goniometer=None)

    #self.reciprocal_lattice_points = mm_spot_pos['rlp'].select(
     #          (self.reflections['id'] == -1)&(1/self.reflections['rlp'].norms() > d_min))

    print "Indexing from %i reflections" %len(self.reciprocal_lattice_points)

    def compute_functional(vector):
      '''computes functional for 2-D grid search'''
      two_pi_S_dot_v = 2 * math.pi * self.reciprocal_lattice_points.dot(vector)
      return flex.sum(flex.cos(two_pi_S_dot_v))

    from rstbx.array_family import flex
    from rstbx.dps_core import SimpleSamplerTool
    assert self.target_symmetry_primitive is not None
    assert self.target_symmetry_primitive.unit_cell() is not None
    SST = SimpleSamplerTool(
      self.params.real_space_grid_search.characteristic_grid)
    SST.construct_hemisphere_grid(SST.incr)
    cell_dimensions = self.target_symmetry_primitive.unit_cell().parameters()[:3]
    unique_cell_dimensions = set(cell_dimensions)

    print("Makring search vecs")
    spiral_method = True
    if spiral_method:
        basis_vec_noise =True
        noise_scale = 2.
        #_N = 200000   # massively oversample the hemisphere so we can apply noise to our search
        _N = 100000
        print "Number of search vectors: %i" %( _N * len(unique_cell_dimensions))
        J = _N*2
        _thetas = [np.arccos( (2.*j - 1. - J)/J)
            for j in range(1,J+1)]
        _phis = [ np.sqrt( np.pi*J) *np.arcsin( (2.*j - 1. - J)/J )
            for j in range(1,J+1)]
        _x = np.sin(_thetas)*np.cos(_phis)
        _y = np.sin(_thetas)*np.sin(_phis)
        _z = np.cos(_thetas)
        nn = int(_N * 1.01)
        _u_vecs = np.array(zip(_x,_y,_z))[-nn:]

        rec_pts = np.array([self.reciprocal_lattice_points[i] for i in range(len(self.reciprocal_lattice_points))])
        N_unique = len(unique_cell_dimensions)

        # much faster to use numpy for massively over-sampled hemisphere..
        func_vals = np.zeros( nn*N_unique)
        vecs = np.zeros( (nn*N_unique, 3) )
        for i, l in enumerate(unique_cell_dimensions):
          # create noise model on top of lattice lengths...
          if basis_vec_noise:
            vec_mag = np.random.normal( l, scale=noise_scale, size=_u_vecs.shape[0] )
            vec_mag = vec_mag[:,None]
          else:
            vec_mag = l

          ul = _u_vecs * vec_mag
          func_slc = slice( i*nn, (i+1)*nn)
          vecs[func_slc] = ul
          func_vals[func_slc] = np.sum( np.cos( 2*np.pi*np.dot(rec_pts, ul.T) ),
                                      axis=0)

        order = np.argsort(func_vals)[::-1]  # sort function values, largest values first
        function_values = func_vals[order]
        vectors = vecs[order]

    else:  # fall back on original flex method
        vectors = flex.vec3_double()
        function_values = flex.double()
        print "Number of search vectors: %i" % (   len(SST.angles)* len(unique_cell_dimensions))
        for i, direction in enumerate(SST.angles):
          for l in unique_cell_dimensions:
            v = matrix.col(direction.dvec) * l
            f = compute_functional(v.elems)
            vectors.append(v.elems)
            function_values.append(f)
        perm = flex.sort_permutation(function_values, reverse=True)
        vectors = vectors.select(perm)
        function_values = function_values.select(perm)

    print("made search vecs")

    unique_vectors = []
    i = 0
    while len(unique_vectors) < N_UNIQUE_V:
      v = matrix.col(vectors[i])
      is_unique = True
      if i > 0:
        for v_u in unique_vectors:
          if v.length() < v_u.length():
            if is_approximate_integer_multiple(v, v_u):
              is_unique = False
              break
          elif is_approximate_integer_multiple(v_u, v):
            is_unique = False
            break
      if is_unique:
        unique_vectors.append(v)
      i += 1

    print ("chose unique basis vecs")
    if self.params.debug:
      for i in range(N_UNIQUE_V):
        v = matrix.col(vectors[i])
        print v.elems, v.length(), function_values[i]

    basis_vectors = [v.elems for v in unique_vectors]
    self.candidate_basis_vectors = basis_vectors

    if self.params.optimise_initial_basis_vectors:
      self.params.optimize_initial_basis_vectors = False
      # todo: verify this reference to self.reciprocal_lattice_points is correct
      optimised_basis_vectors = optimise_basis_vectors(
        self.reciprocal_lattice_points, basis_vectors)
      optimised_function_values = flex.double([
        compute_functional(v) for v in optimised_basis_vectors])

      perm = flex.sort_permutation(optimised_function_values, reverse=True)
      optimised_basis_vectors = optimised_basis_vectors.select(perm)
      optimised_function_values = optimised_function_values.select(perm)

      unique_vectors = [matrix.col(v) for v in optimised_basis_vectors]

    print "Number of unique vectors: %i" %len(unique_vectors)

    if self.params.debug:
      for i in range(len(unique_vectors)):
        print compute_functional(unique_vectors[i].elems), unique_vectors[i].length(), unique_vectors[i].elems
        print

    crystal_models = []
    self.candidate_basis_vectors = unique_vectors

    if self.params.debug:
      self.debug_show_candidate_basis_vectors()
    if self.params.debug_plots:
      self.debug_plot_candidate_basis_vectors()


    candidate_orientation_matrices \
      = self.find_candidate_orientation_matrices(
        unique_vectors)
        # max_combinations=self.params.basis_vector_combinations.max_try)

    FILTER_BY_MAG = True
    if FILTER_BY_MAG:
      print("\n\n FILTERING BY MAG\n\n")
      FILTER_TOL = 10,3   # within 5 percent of params and 1 percent of ang
      target_uc = self.params.known_symmetry.unit_cell.parameters()
      good_mats = []
      for c in candidate_orientation_matrices:
        uc = c.get_unit_cell().parameters()
        comps = []
        for i in range(3):
          tol = 0.01* FILTER_TOL[0] * target_uc[i]
          low = target_uc[i] - tol/2.
          high = target_uc[i] + tol/2
          comps.append(  low < uc[i] < high )
        for i in range(3,6):
          low = target_uc[i] - FILTER_TOL[1]
          high = target_uc[i] + FILTER_TOL[1]
          comps.append( low < uc[i] < high )
        if all( comps):
          print("matrix is ok:", c)
          good_mats.append(c)
        print("\nFilter kept %d / %d mats" % \
              (len(good_mats), len(candidate_orientation_matrices)))
      candidate_orientation_matrices = good_mats

    crystal_model, n_indexed = self.choose_best_orientation_matrix(
      candidate_orientation_matrices)
    orange = 2
    if crystal_model is not None:
      crystal_models = [crystal_model]
    else:
      crystal_models = []

    #assert len(crystal_models) > 0

    candidate_orientation_matrices = crystal_models

    #for i in range(len(candidate_orientation_matrices)):
      #if self.target_symmetry_primitive is not None:
        ##print "symmetrizing model"
        ##self.target_symmetry_primitive.show_summary()
        #symmetrized_model = self.apply_symmetry(
          #candidate_orientation_matrices[i], self.target_symmetry_primitive)
        #candidate_orientation_matrices[i] = symmetrized_model
    self.candidate_crystal_models = candidate_orientation_matrices

    # memory leak somewhere... probably not here.. but just in case...
    del _x, _y, _z, _u_vecs, order, rec_pts, vecs, func_vals, vectors, function_values

