#!/usr/bin/env python3
# -*- coding: utf-8 -*-


#    pept is a Python library that unifies Positron Emission Particle
#    Tracking (PEPT) research, including tracking, simulation, data analysis
#    and visualisation tools.
#
#    If you used this codebase or any software making use of it in a scientific
#    publication, you must cite the following paper:
#        Nicuşan AL, Windows-Yule CR. Positron emission particle tracking
#        using machine learning. Review of Scientific Instruments.
#        2020 Jan 1;91(1):013329.
#        https://doi.org/10.1063/1.5129251
#
#    Copyright (C) 2020 Andrei Leonard Nicusan
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.


# File   : cutpoints.py
# License: GNU v3.0
# Author : Andrei Leonard Nicusan <a.l.nicusan@bham.ac.uk>
# Date   : 13.04.2020


import  time
import  sys
import  os

import  numpy               as      np
from    scipy.spatial       import  cKDTree

from    joblib              import  Parallel, delayed
from    tqdm                import  tqdm

from    concurrent.futures  import  ThreadPoolExecutor, ProcessPoolExecutor

# Fix a deprecation warning inside the sklearn library
try:
    sys.modules['sklearn.externals.six'] = __import__('six')
    sys.modules['sklearn.externals.joblib'] = __import__('joblib')
    import hdbscan
except ImportError:
    import hdbscan

import  pept


def find_cutpoints(
    sample_lines,
    max_distance,
    cutoffs = None,
    append_indices = False
):
    '''Find the cutpoints from a sample / array of LoRs.

    A cutpoint is the point in 3D space that minimises the distance between any
    two lines. For any two non-parallel 3D lines, this point corresponds to the
    midpoint of the unique segment that is perpendicular to both lines.

    This function considers every pair of lines in `sample_lines` and returns
    all the cutpoints that satisfy the following conditions:
        1. The distance between the two lines is smaller than `max_distance`.
        2. The cutpoints is within the `cutoffs`.

    Note that this function uses LoRs that have a single timestamp. If the PEPT
    scanner has Time of Flight (ToF) functionality, such that every line
    is defined by two points with *two individual timestamps*, the
    `find_cutpoints_tof` function should be used.

    Parameters
    ----------
    sample_lines : (N, M >= 7) numpy.ndarray
        A sample of LoRs, where each row is `[time, x1, y1, z1, x2, y2, z2]`,
        such that every line is defined by the points `[x1, y1, z1]` and
        `[x2, y2, z2]`.
    max_distance : float
        The maximum distance between any pair of lines so that their cutpoint
        will be considered.
    cutoffs : list, optional
        The cutoffs for each dimension, formatted as `[x_min, x_max,
        y_min, y_max, z_min, z_max]`. If it is `None`, they are computed
        automatically by calling `get_cutoffs`. The default is `None`.
    append_indices : bool, optional
        If set to `True`, the indices of the individual LoRs that were used
        to compute each cutpoint are also appended to the returned array.
        Default is `False`.

    Returns
    -------
    cutpoints : (M, 4) or (M, 6) numpy.ndarray
        A numpy array of the calculated weighted cutpoints. If `append_indices`
        is `False`, then the columns are [time, x, y, z]. If `append_indices`
        is `True`, then the columns are [time, x, y, z, i, j], where `i` and
        `j` are the LoR indices from `sample_lines` that were used to compute
        the weighted cutpoints. The time is the average between the timestamps
        of the two LoRs that were used to compute the cutpoint. The first
        column (for time) is sorted.

    Raises
    ------
    ValueError
        If `sample_lines` is not a numpy array with shape (N, M >= 7).
    ValueError
        If `cutoffs` is not a one-dimensional array with values
        `[min_x, max_x, min_y, max_y, min_z, max_z]`

    '''

    sample_lines = np.asarray(sample_lines, order = 'C', dtype = float)
    max_distance = float(max_distance)

    # Check sample has shape (N, M >= 7)
    if sample_lines.ndim != 2 or sample_lines.shape[1] < 7:
        raise ValueError((
            "\n[ERROR]: `sample_lines` should have dimensions (M, N), "
            f" where N >= 7. Received {sample_lines.shape}.\n"
        ))

    if cutoffs is None:
        cutoffs = get_cutoffs(sample_lines)
    else:
        cutoffs = np.asarray(cutoffs, order = 'C', dtype = float)
        if cutoffs.ndim != 1 or len(cutoffs) != 6:
            raise ValueError((
                "\n[ERROR]: cutoffs should be a one-dimensional array with "
                "values [min_x, max_x, min_y, max_y, min_z, max_z]. Received "
                f"{cutoffs}.\n"
            ))

    sample_cutpoints = pept.utilities.find_cutpoints(
        sample_lines,
        max_distance,
        cutoffs,
        append_indices = append_indices
    )

    return sample_cutpoints


def get_cutoffs(sample):
    '''Compute the cutoffs from a sample of LoR data.

    It computes the cutoffs from the minimum and maximum values of the LoRs in
    `sample` in each dimension (e.g. the x-dimension is defined by data in
    columns 1 and 4).

    Note that this function uses LoRs that have a single timestamp. If the PEPT
    scanner has Time of Flight (ToF) functionality, such that every line
    is defined by two points with *two individual timestamps*, the
    `get_cutoffs_tof` function should be used.

    Parameters
    ----------
    sample : (N, M >= 7) numpy.ndarray
        A sample of LoRs, where each row is `[time, x1, y1, z1, x2, y2, z2]`,
        such that every line is defined by the points `[x1, y1, z1]` and
        `[x2, y2, z2]`.
    Returns
    -------
    cutoffs : (6,) numpy.ndarray
        The computed cutoffs for each dimension, formatted as
        `[x_min, x_max, y_min, y_max, z_min, z_max]`.

    Raises
    ------
    ValueError
        If `sample` is not a numpy array with shape (N, M >= 7).

    '''

    # Check sample has shape (N, M >= 7)
    if sample.ndim != 2 or sample.shape[1] < 7:
        raise ValueError((
            "\n[ERROR]: `sample_lines` should have dimensions (M, N), "
            f" where N >= 7. Received {sample_lines.shape}.\n"
        ))

    # Compute cutoffs for cutpoints as the (min, max) values of the lines
    # Minimum value of the two points that define a line
    min_x = min(sample[:, 1].min(),
                sample[:, 4].min())
    # Maximum value of the two points that define a line
    max_x = max(sample[:, 1].max(),
                sample[:, 4].max())

    # Minimum value of the two points that define a line
    min_y = min(sample[:, 2].min(),
                sample[:, 5].min())
    # Maximum value of the two points that define a line
    max_y = max(sample[:, 2].max(),
                sample[:, 5].max())

    # Minimum value of the two points that define a line
    min_z = min(sample[:, 3].min(),
                sample[:, 6].min())
    # Maximum value of the two points that define a line
    max_z = max(sample[:, 3].max(),
                sample[:, 6].max())

    cutoffs = np.array([min_x, max_x, min_y, max_y, min_z, max_z],
                       dtype = float)
    return cutoffs


def find_cutpoints_tof(
    sample_lines,
    max_distance,
    cutoffs = None,
    append_indices = False
):
    '''Find the cutpoints from a sample / array of LoRs with two timestamps.

    A cutpoint is the point in 3D space that minimises the distance between any
    two lines. For any two non-parallel 3D lines, this point corresponds to the
    midpoint of the unique segment that is perpendicular to both lines.

    This function considers every pair of lines in `sample_lines` and returns
    all the cutpoints that satisfy the following conditions:
        1. The distance between the two lines is smaller than `max_distance`.
        2. The cutpoints is within the `cutoffs`.

    Note that this function is just an extension to the classic
    `find_cutpoints` procedure, made to use LoRs that have two individual
    timestamps (e.g. PEPT data with Time of Flight data or GATE simulations).
    However, it does NOT use Time of Flight data when computing the cutpoints.
    If your PEPT data does have ToF data, you can use `find_weighted_cutpoints`
    for much better results.

    Parameters
    ----------
    sample_lines : (N, M >= 8) numpy.ndarray
        A sample of LoRs, where each row is
        `[time1, x1, y1, z1, time2, x2, y2, z2]`, such that every line is
        defined by the points `[time1, x1, y1, z1]` and `[time2, x2, y2, z2]`.
    max_distance : float
        The maximum distance between any pair of lines so that their cutpoint
        will be considered.
    cutoffs : list, optional
        The cutoffs for each dimension, formatted as `[x_min, x_max,
        y_min, y_max, z_min, z_max]`. If it is `None`, they are computed
        automatically by calling `get_cutoffs`. The default is `None`.
    append_indices : bool, optional
        If set to `True`, the indices of the individual LoRs that were used
        to compute each cutpoint are also appended to the returned array.
        Default is `False`.

    Returns
    -------
    cutpoints : (M, 4) or (M, 6) numpy.ndarray
        A numpy array of the calculated weighted cutpoints. If `append_indices`
        is `False`, then the columns are [time, x, y, z]. If `append_indices`
        is `True`, then the columns are [time, x, y, z, i, j], where `i` and
        `j` are the LoR indices from `sample_lines` that were used to compute
        the weighted cutpoints. The time is the average between the timestamps
        of the two LoRs that were used to compute the cutpoint. The first
        column (for time) is sorted.

    Raises
    ------
    ValueError
        If `sample_lines` is not a numpy array with shape (N, M >= 8).
    ValueError
        If `cutoffs` is not a one-dimensional array with values
        `[min_x, max_x, min_y, max_y, min_z, max_z]`

    '''

    sample_lines = np.asarray(sample_lines, order = 'C', dtype = float)
    max_distance = float(max_distance)

    # Check sample has shape (N, M >= 8)
    if sample_lines.ndim != 2 or sample_lines.shape[1] < 8:
        raise ValueError((
            "\n[ERROR]: `sample_lines` should have dimensions (M, N), "
            f" where N >= 8. Received {sample_lines.shape}.\n"
        ))

    if cutoffs is None:
        cutoffs = get_cutoffs_tof(sample_lines)
    else:
        cutoffs = np.asarray(cutoffs, order = 'C', dtype = float)
        if cutoffs.ndim != 1 or len(cutoffs) != 6:
            raise ValueError((
                "\n[ERROR]: cutoffs should be a one-dimensional array with "
                "values [min_x, max_x, min_y, max_y, min_z, max_z]. Received "
                f"{cutoffs}.\n"
            ))

    sample_cutpoints = pept.utilities.find_cutpoints_tof(
        sample_lines,
        max_distance,
        cutoffs,
        append_indices = append_indices
    )

    return sample_cutpoints


def get_cutoffs_tof(sample):
    '''Compute the cutoffs from a sample of LoR data with two timestamps.

    It computes the cutoffs from the minimum and maximum values of the LoRs in
    `sample` in each dimension (e.g. the x-dimension is defined by data in
    columns 1 and 5).

    Note that this function uses LoRs that have two timestamps. If the PEPT
    scanner does not have Time of Flight (ToF) functionality, such that every
    line is defined by two points with *a single timestamp*, the
    `get_cutoffs` function should be used.

    Parameters
    ----------
    sample : (N, M >= 8) numpy.ndarray
        A sample of LoRs, where each row is
        `[time1, x1, y1, z1, time2, x2, y2, z2]`, such that every line is
        defined by the points `[time1, x1, y1, z1]` and `[time2, x2, y2, z2]`.
    Returns
    -------
    cutoffs : (6,) numpy.ndarray
        The computed cutoffs for each dimension, formatted as
        `[x_min, x_max, y_min, y_max, z_min, z_max]`.

    Raises
    ------
    ValueError
        If `sample` is not a numpy array with shape (N, M >= 8).

    '''

    # Check sample has shape (N, M >= 8)
    if sample.ndim != 2 or sample.shape[1] < 8:
        raise ValueError((
            "\n[ERROR]: `sample_lines` should have dimensions (M, N), "
            f" where N >= 8. Received {sample_lines.shape}.\n"
        ))

    # Compute cutoffs for cutpoints as the (min, max) values of the lines
    # Minimum value of the two points that define a line
    min_x = min(sample[:, 1].min(),
                sample[:, 5].min())
    # Maximum value of the two points that define a line
    max_x = max(sample[:, 1].max(),
                sample[:, 5].max())

    # Minimum value of the two points that define a line
    min_y = min(sample[:, 2].min(),
                sample[:, 6].min())
    # Maximum value of the two points that define a line
    max_y = max(sample[:, 2].max(),
                sample[:, 6].max())

    # Minimum value of the two points that define a line
    min_z = min(sample[:, 3].min(),
                sample[:, 7].min())
    # Maximum value of the two points that define a line
    max_z = max(sample[:, 3].max(),
                sample[:, 7].max())

    cutoffs = np.array([min_x, max_x, min_y, max_y, min_z, max_z],
                       dtype = float)
    return cutoffs




class Cutpoints(pept.PointData):
    '''A class that transforms LoRs (a pept.LineData instance) into *cutpoints*
    (a pept.PointData instance) for clustering.

    The `Cutpoints` class transforms LoRs (encapsulated in a `pept.LineData`
    instance) into cutpoints that can then be passed to `HDBSCANClusterer`. The
    cutpoints are themselves encapsulated in a `pept.PointData` instance.

    Under typical usage, the `Cutpoints` class is initialised with a
    `pept.LineData` instance, automatically calculating the cutpoints. The
    `Cutpoints` class inherits from `pept.PointData`, such that once the
    cutpoints have been computed, all the methods from the parent class
    `pept.PointData` can be used on them (such as visualisation functionality).

    For more control over the operations, `pept.tracking.peptml.find_cutpoints`
    can be used - it receives a generic numpy array of LoRs (one 'sample') and
    returns a numpy array of cutpoints.

    Note that this class uses LoRs that have a single timestamp. If the PEPT
    scanner has Time of Flight (ToF) functionality, such that every line
    is defined by two points with *two individual timestamps*, the
    `CutpointsToF` class should be used.

    Parameters
    ----------
    line_data : instance of pept.LineData
        The LoRs for which the cutpoints will be computed. It must be an
        instance of `pept.LineData`.
    max_distance : float
        The maximum distance between any two LoRs for their cutpoint to be
        considered.
    cutoffs : list-like of length 6, optional
        A list (or equivalent) of the cutoff distances for every axis,
        formatted as `[x_min, x_max, y_min, y_max, z_min, z_max]`. Only the
        cutpoints which fall within these cutoff distances are considered. The
        default is None, in which case they are automatically computed using
        `pept.tracking.peptml.get_cutoffs`.
    append_indices : bool, optional
        If set to `True`, the indices of the individual LoRs that were used
        to compute each cutpoint are also appended to the returned array.
        Default is `False`.
    max_workers : int, optional
        The maximum number of threads that will be used for asynchronously
        computing the cutpoints from the samples of LoRs in `line_data`.
    verbose : bool, optional
        Provide extra information when computing the cutpoints: time the
        operation and show a progress bar. The default is `True`.

    Attributes
    ----------
    line_data : instance of pept.LineData
        The LoRs for which the cutpoints will be computed. It must be an
        instance of `pept.LineData`.
    max_distance : float
        The maximum distance between any two lines for their cutpoint to be
        considered.
    cutoffs : list-like of length 6
        A list (or equivalent) of the cutoff distances for every axis,
        formatted as `[x_min, x_max, y_min, y_max, z_min, z_max]`. Only the
        cutpoints which fall within these cutoff distances are considered. The
        default is None, in which case they are automatically computed using
        `pept.tracking.peptml.get_cutoffs`.
    sample_size, overlap, number_of_lines, etc. : inherited from pept.PointData
        Extra attributes and methods are inherited from the base class
        `PointData`.

    Raises
    ------
    TypeError
        If `line_data` is not an instance of `pept.LineData`.
    ValueError
        If `cutoffs` is not a one-dimensional array with values formatted as
        `[min_x, max_x, min_y, max_y, min_z, max_z]`.

    Example usage
    -------------
    Compute the cutpoints for a `LineData` instance:
        >>> line_data = pept.LineData(example_data)
        >>> cutpts = peptml.Cutpoints(line_data, 0.1)

    Compute the cutpoints for a single sample:
        >>> sample = line_data[0]
        >>> cutpts_sample = peptml.find_cutpoints(sample)

    '''

    def __init__(
        self,
        line_data,
        max_distance,
        cutoffs = None,
        append_indices = False,
        max_workers = None,
        verbose = True
    ):
        # Find the cutpoints when instantiated. The method
        # also initialises the instance as a `PointData` subclass.
        self.find_cutpoints(
            line_data,
            max_distance,
            cutoffs = cutoffs,
            append_indices = append_indices,
            max_workers = max_workers,
            verbose = verbose
        )


    @property
    def line_data(self):
        '''The samples of LoRs for which the cutpoints are computed.

        line_data : instance of pept.LineData

        '''
        return self._line_data


    @property
    def max_distance(self):
        '''The maximum distance between any pair of lines for which their
        cutpoint is considered.

        max_distance : float
            The maximum distance between any two lines for their cutpoint to be
            considered.

        '''
        return self._max_distance


    @max_distance.setter
    def max_distance(self, new_max_distance):
        '''The maximum distance between any pair of lines for which their
        cutpoint is considered.

        max_distance : float
            The maximum distance between any two lines for their cutpoint to be
            considered.

        '''
        self._max_distance = new_max_distance


    @property
    def cutoffs(self):
        '''Only consider the cutpoints which fall within these cutoff distances.

        A list (or equivalent) of the cutoff distances for every axis,
        formatted as [x_min, x_max, y_min, y_max, z_min, z_max].

        cutoffs : (6) list or equivalent

        '''

        return self._cutoffs


    @cutoffs.setter
    def cutoffs(self, new_cutoffs):
        '''Only consider the cutpoints which fall within these cutoff distances.

        A list (or equivalent) of the cutoff distances for every axis,
        formatted as [x_min, x_max, y_min, y_max, z_min, z_max].

        Parameters
        ----------
        new_cutoffs : list-like of length 6, optional
            A list (or equivalent) of the cutoff distances for every axis,
            formatted as [x_min, x_max, y_min, y_max, z_min, z_max]. Only
            consider the cutpoints which fall within these cutoff distances.

        Raises
        ------
        ValueError
            If `cutoffs` is not a one-dimensional array with values formatted
            as `[min_x, max_x, min_y, max_y, min_z, max_z]`.

        '''

        cutoffs = np.asarray(new_cutoffs, order = 'C', dtype = float)
        if cutoffs.ndim != 1 or len(cutoffs) != 6:
            raise ValueError((
                "\n[ERROR]: cutoffs should be a one-dimensional array "
                "with values [min_x, max_x, min_y, max_y, min_z, max_z]. "
                f"Received {cutoffs}.\n"
            ))

        self._cutoffs = cutoffs


    def find_cutpoints(
        self,
        line_data,
        max_distance,
        cutoffs = None,
        append_indices = False,
        max_workers = None,
        verbose = False
    ):
        '''Find the cutpoints of the samples in a `LineData` instance.

        Parameters
        ----------
        line_data : instance of pept.LineData
            The LoRs for which the cutpoints will be computed. It must be an
            instance of `pept.LineData`.
        max_distance : float
            The maximum distance between any two LoRs for their cutpoint to be
            considered.
        cutoffs : list-like of length 6, optional
            A list (or equivalent) of the cutoff distances for every axis,
            formatted as `[x_min, x_max, y_min, y_max, z_min, z_max]`. Only the
            cutpoints which fall within these cutoff distances are considered.
            The default is None, in which case they are automatically computed
            using `pept.tracking.peptml.get_cutoffs`.
        append_indices : bool, optional
            If set to `True`, the indices of the individual LoRs that were used
            to compute each cutpoint are also appended to the returned array.
            Default is `False`.
        max_workers : int, optional
            The maximum number of threads that will be used for asynchronously
            computing the cutpoints from the samples of LoRs in `line_data`.
        verbose : bool, optional
            Provide extra information when computing the cutpoints: time the
            operation and show a progress bar. The default is `True`.

        Returns
        -------
        self : the PointData instance of cutpoints
            The computed cutpoints are stored in the `Cutpoints` class, as a
            subclass of `pept.PointData`.

        Raises
        ------
        TypeError
            If `line_data` is not an instance of `pept.LineData`.
        ValueError
            If `cutoffs` is not a one-dimensional array with values formatted
            as `[min_x, max_x, min_y, max_y, min_z, max_z]`.

        '''

        if verbose:
            start = time.time()

        # Check line_data is an instance (or a subclass!) of pept.LineData
        if not isinstance(line_data, pept.LineData):
            raise TypeError((
                "\n[ERROR]: line_data should be an instance (or subclass) of "
                "`pept.LineData`.\n"
            ))

        self._line_data = line_data
        self._max_distance = float(max_distance)

        # If cutoffs were not supplied, compute them
        if cutoffs is None:
            cutoffs = get_cutoffs(line_data.line_data)
        # Otherwise make sure they are a C-contiguous numpy array
        else:
            cutoffs = np.asarray(cutoffs, order = 'C', dtype = float)
            if cutoffs.ndim != 1 or len(cutoffs) != 6:
                raise ValueError((
                    "\n[ERROR]: cutoffs should be a one-dimensional array "
                    "with values [min_x, max_x, min_y, max_y, min_z, max_z]. "
                    f"Received {cutoffs}.\n"
                ))

        self._cutoffs = cutoffs

        # Using ThreadPoolExecutor, asynchronously collect the cutpoints from
        # every sample in a list of arrays. This is more efficient than using
        # ProcessPoolExecutor because find_cutpoints is a Cython function that
        # releases the GIL for most of its computation.
        # If verbose, show progress bar using tqdm.
        if max_workers is None:
            max_workers = os.cpu_count()

        with ThreadPoolExecutor(max_workers = max_workers) as executor:
            futures = []
            for sample in line_data:
                futures.append(
                    executor.submit(
                        pept.utilities.find_cutpoints,
                        sample,
                        max_distance,
                        cutoffs,
                        append_indices = append_indices
                    )
                )

            if verbose:
                futures = tqdm(futures)

            cutpoints = [f.result() for f in futures]

        # cutpoints shape: (n, m, 4), where n is the number of samples, and
        # m is the number of cutpoints in the sample
        cutpoints = np.array(cutpoints)

        number_of_samples = len(cutpoints)
        cutpoints = np.vstack(cutpoints)
        number_of_cutpoints = len(cutpoints)

        # Average number of cutpoints per sample
        cutpoints_per_sample = int(number_of_cutpoints / number_of_samples)

        pept.PointData.__init__(
            self,
            cutpoints,
            sample_size = cutpoints_per_sample,
            overlap = 0,
            verbose = False
        )

        if verbose:
            end = time.time()
            print(f"\nFinding the cutpoints took {end - start} seconds.\n")

        return self




class CutpointsToF(pept.PointData):
    '''A class that transforms LoRs with ToF data (a pept.LineDataToF instance)
    into *cutpoints* (a pept.PointData instance) for clustering.

    The `Cutpoints` class transforms LoRs (encapsulated in a `pept.LineDataToF`
    instance) into cutpoints that can then be passed to `HDBSCANClusterer`. The
    cutpoints are themselves encapsulated in a `pept.PointData` instance.

    Under typical usage, the `Cutpoints` class is initialised with a
    `pept.LineDataToF` instance, automatically calculating the cutpoints. The
    `Cutpoints` class inherits from `pept.PointData`, such that once the
    cutpoints have been computed, all the methods from the parent class
    `pept.PointData` can be used on them (such as visualisation functionality).

    For more control over the operations,
    `pept.tracking.peptml.find_cutpoints_tof` can be used - it receives a
    generic numpy array of LoRs (one 'sample') and returns a numpy array of
    cutpoints.

    Note that this class uses LoRs that have Time of Flight (ToF) data, such
    that every line is defined by two points with *two individual timestamps*.
    If the PEPT scanner does not have ToF functionality (i.e. the LoRs have
    single timestamps), the `Cutpoints` class should be used.

    Parameters
    ----------
    line_data : instance of pept.LineDataToF
        The LoRs for which the cutpoints will be computed. It must be an
        instance of `pept.LineDataToF`.
    max_distance : float
        The maximum distance between any two LoRs for their cutpoint to be
        considered.
    cutoffs : list-like of length 6, optional
        A list (or equivalent) of the cutoff distances for every axis,
        formatted as `[x_min, x_max, y_min, y_max, z_min, z_max]`. Only the
        cutpoints which fall within these cutoff distances are considered. The
        default is None, in which case they are automatically computed using
        `pept.tracking.peptml.get_cutoffs`.
    append_indices : bool, optional
        If set to `True`, the indices of the individual LoRs that were used
        to compute each cutpoint are also appended to the returned array.
        Default is `False`.
    max_workers : int, optional
        The maximum number of threads that will be used for asynchronously
        computing the cutpoints from the samples of LoRs in `line_data`.
    verbose : bool, optional
        Provide extra information when computing the cutpoints: time the
        operation and show a progress bar. The default is `True`.

    Attributes
    ----------
    line_data : instance of pept.LineDataToF
        The LoRs for which the cutpoints will be computed. It must be an
        instance of `pept.LineDataToF`.
    max_distance : float
        The maximum distance between any two lines for their cutpoint to be
        considered.
    cutoffs : list-like of length 6
        A list (or equivalent) of the cutoff distances for every axis,
        formatted as `[x_min, x_max, y_min, y_max, z_min, z_max]`. Only the
        cutpoints which fall within these cutoff distances are considered. The
        default is None, in which case they are automatically computed using
        `pept.tracking.peptml.get_cutoffs_tof`.
    sample_size, overlap, number_of_lines, etc. : inherited from pept.PointData
        Extra attributes and methods are inherited from the base class
        `PointData`.

    Raises
    ------
    TypeError
        If `line_data` is not an instance of `pept.LineDataToF`.
    ValueError
        If `cutoffs` is not a one-dimensional array with values formatted as
        `[min_x, max_x, min_y, max_y, min_z, max_z]`.

    Example usage
    -------------
    Compute the cutpoints for a `LineDataToF` instance:
        >>> line_data = pept.LineDataToF(example_data)
        >>> cutpts = peptml.CutpointsToF(line_data, 0.1)

    Compute the cutpoints for a single sample:
        >>> sample = line_data[0]
        >>> cutpts_sample = peptml.find_cutpoints_tof(sample)

    '''

    def __init__(
        self,
        line_data,
        max_distance,
        cutoffs = None,
        append_indices = False,
        max_workers = None,
        verbose = True
    ):
        # Find the cutpoints when instantiated. The method
        # also initialises the instance as a `PointData` subclass.
        self.find_cutpoints(
            line_data,
            max_distance,
            cutoffs = cutoffs,
            append_indices = append_indices,
            max_workers = max_workers,
            verbose = verbose
        )


    @property
    def line_data(self):
        '''The samples of LoRs for which the cutpoints are computed.

        line_data : instance of pept.LineDataToF

        '''
        return self._line_data


    @property
    def max_distance(self):
        '''The maximum distance between any pair of lines for which their
        cutpoint is considered.

        max_distance : float
            The maximum distance between any two lines for their cutpoint to be
            considered.

        '''
        return self._max_distance


    @max_distance.setter
    def max_distance(self, new_max_distance):
        '''The maximum distance between any pair of lines for which their
        cutpoint is considered.

        max_distance : float
            The maximum distance between any two lines for their cutpoint to be
            considered.

        '''
        self._max_distance = new_max_distance


    @property
    def cutoffs(self):
        '''Only consider the cutpoints which fall within these cutoff distances.

        A list (or equivalent) of the cutoff distances for every axis,
        formatted as [x_min, x_max, y_min, y_max, z_min, z_max].

        cutoffs : (6) list or equivalent

        '''

        return self._cutoffs


    @cutoffs.setter
    def cutoffs(self, new_cutoffs):
        '''Only consider the cutpoints which fall within these cutoff
        distances.

        A list (or equivalent) of the cutoff distances for every axis,
        formatted as [x_min, x_max, y_min, y_max, z_min, z_max].

        Parameters
        ----------
        new_cutoffs : list-like of length 6, optional
            A list (or equivalent) of the cutoff distances for every axis,
            formatted as [x_min, x_max, y_min, y_max, z_min, z_max]. Only
            consider the cutpoints which fall within these cutoff distances.

        Raises
        ------
        ValueError
            If `cutoffs` is not a one-dimensional array with values formatted
            as `[min_x, max_x, min_y, max_y, min_z, max_z]`.

        '''

        cutoffs = np.asarray(new_cutoffs, order = 'C', dtype = float)
        if cutoffs.ndim != 1 or len(cutoffs) != 6:
            raise ValueError((
                "\n[ERROR]: cutoffs should be a one-dimensional array "
                "with values [min_x, max_x, min_y, max_y, min_z, max_z]. "
                f"Received {cutoffs}.\n"
            ))

        self._cutoffs = cutoffs


    def find_cutpoints(
        self,
        line_data,
        max_distance,
        cutoffs = None,
        append_indices = False,
        max_workers = None,
        verbose = False
    ):
        '''Find the cutpoints of the samples in a `LineDataToF` instance.

        Parameters
        ----------
        line_data : instance of pept.LineDataToF
            The LoRs for which the cutpoints will be computed. It must be an
            instance of `pept.LineDataToF`.
        max_distance : float
            The maximum distance between any two LoRs for their cutpoint to be
            considered.
        cutoffs : list-like of length 6, optional
            A list (or equivalent) of the cutoff distances for every axis,
            formatted as `[x_min, x_max, y_min, y_max, z_min, z_max]`. Only the
            cutpoints which fall within these cutoff distances are considered.
            The default is None, in which case they are automatically computed
            using `pept.tracking.peptml.get_cutoffs_tof`.
        append_indices : bool, optional
            If set to `True`, the indices of the individual LoRs that were used
            to compute each cutpoint are also appended to the returned array.
            Default is `False`.
        max_workers : int, optional
            The maximum number of threads that will be used for asynchronously
            computing the cutpoints from the samples of LoRs in `line_data`.
        verbose : bool, optional
            Provide extra information when computing the cutpoints: time the
            operation and show a progress bar. The default is `True`.

        Returns
        -------
        self : the PointData instance of cutpoints
            The computed cutpoints are stored in the `Cutpoints` class, as a
            subclass of `pept.PointData`.

        Raises
        ------
        TypeError
            If `line_data` is not an instance of `pept.LineDataToF`.
        ValueError
            If `cutoffs` is not a one-dimensional array with values formatted
            as `[min_x, max_x, min_y, max_y, min_z, max_z]`.

        '''

        if verbose:
            start = time.time()

        # Check line_data is an instance (or a subclass!) of pept.LineDataToF
        if not isinstance(line_data, pept.LineDataToF):
            raise TypeError((
                "\n[ERROR]: line_data should be an instance (or subclass) of "
                "pept.LineDataToF.\n"
            ))

        self._line_data = line_data
        self._max_distance = float(max_distance)

        # If cutoffs were not supplied, compute them
        if cutoffs is None:
            cutoffs = get_cutoffs_tof(line_data.line_data)
        # Otherwise make sure they are a C-contiguous numpy array
        else:
            cutoffs = np.asarray(cutoffs, order = 'C', dtype = float)
            if cutoffs.ndim != 1 or len(cutoffs) != 6:
                raise ValueError((
                    "\n[ERROR]: cutoffs should be a one-dimensional array "
                    "with values [min_x, max_x, min_y, max_y, min_z, max_z]. "
                    f"Received {cutoffs}.\n"
                ))

        self._cutoffs = cutoffs

        # Using ThreadPoolExecutor, asynchronously collect the cutpoints from
        # every sample in a list of arrays. This is more efficient than using
        # ProcessPoolExecutor because find_cutpoints is a Cython function that
        # releases the GIL for most of its computation.
        # If verbose, show progress bar using tqdm.
        if max_workers is None:
            max_workers = os.cpu_count()

        with ThreadPoolExecutor(max_workers = max_workers) as executor:
            futures = []
            for sample in line_data:
                futures.append(
                    executor.submit(
                        pept.utilities.find_cutpoints_tof,
                        sample,
                        max_distance,
                        cutoffs,
                        append_indices = append_indices
                    )
                )

            if verbose:
                futures = tqdm(futures)

            cutpoints = [f.result() for f in futures]

        # cutpoints shape: (n, m, 4), where n is the number of samples, and
        # m is the number of cutpoints in the sample
        cutpoints = np.array(cutpoints)

        number_of_samples = len(cutpoints)
        cutpoints = np.vstack(cutpoints)
        number_of_cutpoints = len(cutpoints)

        # Average number of cutpoints per sample
        cutpoints_per_sample = int(number_of_cutpoints / number_of_samples)

        pept.PointData.__init__(
            self,
            cutpoints,
            sample_size = cutpoints_per_sample,
            overlap = 0,
            verbose = False
        )

        if verbose:
            end = time.time()
            print(f"\nFinding the cutpoints took {end - start} seconds.\n")

        return self

