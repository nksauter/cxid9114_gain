import numpy as np

from dxtbx.model import Detector
from dials.array_family import flex


def make_dials_cspad(psf):
    """
    :param psf: psf as it would roll out of psana geometry object
    :return:
    """
    origins, slows, fasts = psf / 1000.  # convert from psgeom units (microns) to dials units (mm)
    print("init cspad")
    CSPAD = Detector()
    QUADS = {i_quad : CSPAD.add_group() for i_quad in range(4)}  # make the quadrants as dials detector groups
    for i_quad in range(4):
        add_asics_to_quad(quad=QUADS[i_quad],
                          quad_number=i_quad,
                          asic_origins=origins,
                          asic_ss=slows,
                          asic_fs=fasts)
        QUADS[i_quad].set_name("quad%d"%i_quad)
    return CSPAD

def add_asics_to_quad(quad, quad_number, asic_origins, asic_ss, asic_fs):
    for _i in range(16):  # 16 asics per quad, each two are a bonded pair
        i_a = quad_number*16 + _i  # master index of the asic
        A = quad.add_panel()  # add each asic as a panel
        ss = asic_ss[i_a]  # asics slow-scan
        fs = asic_fs[i_a]  # asics fast-scan direction
        orig = asic_origins[i_a]  # asics corner from which fs and ss originate

        A.set_local_frame(tuple(fs), tuple(ss), tuple(orig))  # still not sure bout this...
        A.set_pixel_size((0.10992, 0.10992))  # standard for cspad
        A.set_image_size((194, 185))  # standard for cspad
        A.set_trusted_range((-56, 65000))  # standard for cspad
        A.set_name("asic%d" % i_a)  # name them according to master-array index (0-63)


def psana_geom_splitter(psf, returned_units='mm'):
    """
    Splits the psana geometry from 32 panels to 64 panels
    because of the non-uniform pixel gap
    This creates a list of 64 origins, 64 slow-scan directions and 64 fast-scan directions
    though slow and fast scan directions are assumed parallel within
    manufacturing error
    :param psf: return value of method get_psf() for any cspads geometryAccess instance <PSCalib.GeometryAccess.GeometryAccess>
    :param event: psana.Event instatance, can usually just leave it as None`
    :param returned_units: string of 'mm', 'um', or 'pixels'
    :return: PSF vectors, 64 long
    """
    #geom = cspad.geometry(event)
    origin_64 = np.zeros((64, 3))
    FS_64 = np.zeros_like(origin_64)
    SS_64 = np.zeros_like(origin_64)

    origin_32, SS_32, FS_32 = map(np.array, zip(*psf))
    for i in range(32):
        # create the origins of each sub asic
        origin_A = origin_32[i]
        shift = 194. * 109.92 + (274.8 - 109.92) * 2.
        unit_f = FS_32[i] / np.linalg.norm(FS_32[i])
        origin_B = origin_A + unit_f * shift

        # save two sub-asics per each of the 32 actual asics
        idx_A = 2 * i
        idx_B = 2 * i + 1
        origin_64[idx_A] = origin_A
        origin_64[idx_B] = origin_B
        FS_64[idx_A] = FS_64[idx_B] = FS_32[i]
        SS_64[idx_A] = SS_64[idx_B] = SS_32[i]

    if returned_units == "mm":  # dials convention
        return origin_64 / 1000., SS_64 / 1000., FS_64 / 1000.,
    elif returned_units == "um":  # psgeom convention
        return origin_64, SS_64, FS_64
    elif returned_units == "pixels":  # crystfel convention
        return origin_64 / 109.92, SS_64 / 109.92, FS_64 / 109.92

def psana_data_to_aaron64_data(data, as_flex=False):
    """
    :param data:  32 x 185 x 388 cspad data
    :return: 64 x 185 x 194 cspad data
    """
    asics = []
    # check if necessary to convert to float 64
    dtype = data.dtype
    if as_flex and dtype != np.float64:
        dtype = np.float64
    for split_asic in [(asic[:, :194], asic[:, 194:]) for asic in data]:
        for sub_asic in split_asic:  # 185x194 arrays
            if as_flex:
                sub_asic = np.ascontiguousarray(sub_asic, dtype=dtype)  # ensure contiguous arrays for flex
                sub_asic = flex.double(sub_asic)  # flex data beith double
            asics.append(sub_asic)
    if as_flex:
        asics = tuple(asics)
    return asics
