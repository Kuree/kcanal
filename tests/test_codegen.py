import subprocess

from kcanal.circuit import CB, SB, TileCircuit
from kcanal.interconnect import Interconnect
from kcanal.util import DummyCore, create_uniform_interconnect, SwitchBoxType
from kcanal.cyclone import PortNode, Node, ImranSwitchBox, DisjointSwitchBox, Tile, SwitchBoxSide, SwitchBoxIO, \
    SBConnectionType, SwitchBox

import kratos
import shutil
import tempfile
import pytest
import os

iverilog_available = shutil.which("iverilog") is not None


def check_verilog(filename):
    subprocess.check_call(["iverilog", os.path.basename(filename), "-g2012"], cwd=os.path.dirname(filename),
                          stdout=None)


def insert_pipeline_registers(sb: SwitchBox):
    for side in SwitchBoxSide:
        for track in range(sb.num_track):
            sb.add_pipeline_register(side, track)


@pytest.mark.skipif(not iverilog_available, reason="iverilog not available")
def test_cb_codegen():
    node = PortNode("test", 0, 0, 16)
    for _ in range(5):
        Node(0, 0, 16).add_edge(node)

    cb = CB(node, 32, 32)
    cb.finalize()
    with tempfile.TemporaryDirectory() as temp:
        filename = os.path.join(temp, "cb.sv")
        kratos.verilog(cb, filename=filename)
        check_verilog(filename)


@pytest.mark.skipif(not iverilog_available, reason="iverilog not available")
@pytest.mark.parametrize("insert_pipline", [True, False])
def test_sb_codegen(insert_pipline):
    switchbox = ImranSwitchBox(0, 0, 2, 1)
    if insert_pipline:
        insert_pipeline_registers(switchbox)
    sb = SB(switchbox, 8, 32, "Test")
    sb.finalize()
    with tempfile.TemporaryDirectory() as temp:
        filename = os.path.join(temp, "sb.sv")
        kratos.verilog(sb, filename=filename)
        check_verilog(filename)


def get_in_out_connections(num_tracks):
    input_connections = []
    for track in range(num_tracks):
        for side in SwitchBoxSide:
            input_connections.append(SBConnectionType(side, track,
                                                      SwitchBoxIO.SB_IN))
    output_connections = []
    for track in range(num_tracks):
        for side in SwitchBoxSide:
            output_connections.append(SBConnectionType(side, track,
                                                       SwitchBoxIO.SB_OUT))
    return input_connections, output_connections


@pytest.mark.skipif(not iverilog_available, reason="iverilog not available")
def test_tile_codegen():
    x, y = 0, 0
    tiles = {}
    bit_widths = [1, 16]
    num_tracks = 5
    addr_width = 8
    data_width = 32
    tile_id_width = 16

    for bit_width in bit_widths:
        # we use disjoint switch here
        switchbox = DisjointSwitchBox(x, y, num_tracks, bit_width)
        tile = Tile(x, y, bit_width, switchbox)
        tiles[bit_width] = tile

    input_connections, output_connections = get_in_out_connections(num_tracks)

    core = DummyCore(addr_width, data_width)

    for bit_width, tile in tiles.items():
        tile.set_core(core)

        input_port_name = f"in{bit_width}"
        input_port_name_extra = f"data_in_{bit_width}b_extra"
        output_port_name = f"out{bit_width}"

        tile.set_core_connection(input_port_name, input_connections)
        tile.set_core_connection(output_port_name, output_connections)

    tile_circuit = TileCircuit(tiles, addr_width, data_width,
                               tile_id_width=tile_id_width)
    tile_circuit.finalize()
    with tempfile.TemporaryDirectory() as temp:
        filename = os.path.join(temp, "tile.sv")
        kratos.verilog(tile_circuit, filename=filename)
        check_verilog(filename)


def test_interconnect_codegen():
    addr_width = 8
    data_width = 32
    bit_widths = [1, 16]
    tile_id_width = 16
    track_length = 1
    chip_size = 2
    num_tracks = 5
    # creates all the cores here
    # we don't want duplicated cores when snapping into different interconnect
    # graphs
    cores = {}
    core_type = DummyCore
    for x in range(chip_size):
        for y in range(chip_size):
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
        ic = create_uniform_interconnect(chip_size, chip_size, bit_width,
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
    with tempfile.TemporaryDirectory() as temp:
        filename = os.path.join(temp, "interconnect.sv")
        kratos.verilog(interconnect, filename=filename)
        check_verilog(filename)


if __name__ == "__main__":
    test_interconnect_codegen()
