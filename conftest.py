import pytest
import kratos

from kcanal.util import create_uniform_interconnect, DummyCore, SwitchBoxType
from kcanal.cyclone import SwitchBoxIO, SwitchBoxSide
from kcanal.interconnect import Interconnect


@pytest.fixture(autouse=True)
def clear_kratos_context():
    kratos.Generator.clear_context()


def create_dummy_interconnect_fn(chip_size_x=2, chip_size_y=2, num_tracks=5):
    addr_width = 8
    data_width = 32
    bit_widths = [1, 16]
    tile_id_width = 16
    track_length = 1
    # creates all the cores here
    # we don't want duplicated cores when snapping into different interconnect
    # graphs
    cores = {}
    core_type = DummyCore
    for x in range(chip_size_x):
        for y in range(chip_size_y):
            cores[(x, y)] = core_type()

    def create_core(xx: int, yy: int):
        return cores[(xx, yy)]

    in_conn = []
    out_conn = []
    for side in SwitchBoxSide:
        in_conn.append((side, SwitchBoxIO.SB_IN))
        out_conn.append((side, SwitchBoxIO.SB_OUT))
    pipeline_regs = []
    for track in range(num_tracks):
        for side in SwitchBoxSide:
            pipeline_regs.append((track, side))
    ics = {}
    for bit_width in bit_widths:
        ic = create_uniform_interconnect(chip_size_x, chip_size_y, bit_width,
                                         create_core,
                                         {f"in{bit_width}": in_conn,
                                          f"out{bit_width}": out_conn},
                                         {track_length: num_tracks},
                                         SwitchBoxType.Disjoint,
                                         pipeline_regs)
        ics[bit_width] = ic
    interconnect = Interconnect(ics, addr_width, data_width, tile_id_width,
                                lift_ports=True)
    # finalize the design
    interconnect.finalize()
    return interconnect


@pytest.fixture(autouse=True)
def create_dummy_interconnect():
    return create_dummy_interconnect_fn
