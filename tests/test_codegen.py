import subprocess

from kcanal.circuit import CB, SB, TileCircuit
from kcanal.interconnect import Interconnect
from kcanal.util import DummyCore, create_uniform_interconnect, SwitchBoxType
from kcanal.cyclone import PortNode, Node, ImranSwitchBox, DisjointSwitchBox, Tile, SwitchBoxSide, SwitchBoxIO, \
    SBConnectionType, SwitchBox

import kratos
import tempfile
import pytest
import os


def check_verilog(mod, filename):
    options = kratos.SystemVerilogCodeGenOptions()
    # iverilog doesn't like unique case
    options.unique_case = False
    kratos.verilog(mod, filename=filename, codegen_options=options)
    subprocess.check_call(["iverilog", os.path.basename(filename), "-g2012"], cwd=os.path.dirname(filename),
                          stdout=None)


def insert_pipeline_registers(sb: SwitchBox):
    for side in SwitchBoxSide:
        for track in range(sb.num_track):
            sb.add_pipeline_register(side, track)


def test_cb_codegen():
    node = PortNode("test", 0, 0, 16)
    for _ in range(5):
        Node(0, 0, 16).add_edge(node)

    cb = CB(node, 32, 32)
    cb.finalize()
    with tempfile.TemporaryDirectory() as temp:
        filename = os.path.join(temp, "cb.sv")
        check_verilog(cb, filename)


@pytest.mark.parametrize("insert_pipline", [True, False])
def test_sb_codegen(insert_pipline):
    switchbox = ImranSwitchBox(0, 0, 2, 1)
    if insert_pipline:
        insert_pipeline_registers(switchbox)
    sb = SB(switchbox, 8, 32, "Test")
    sb.finalize()
    with tempfile.TemporaryDirectory() as temp:
        filename = os.path.join(temp, "sb.sv")
        check_verilog(sb, filename)


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
        output_port_name = f"out{bit_width}"

        tile.set_core_connection(input_port_name, input_connections)
        tile.set_core_connection(output_port_name, output_connections)

    tile_circuit = TileCircuit(tiles, addr_width, data_width,
                               tile_id_width=tile_id_width)
    tile_circuit.finalize()
    with tempfile.TemporaryDirectory() as temp:
        filename = os.path.join(temp, "tile.sv")
        check_verilog(tile_circuit, filename)


def test_interconnect_codegen(create_dummy_interconnect):
    chip_size = 2
    interconnect = create_dummy_interconnect(chip_size, chip_size)
    with tempfile.TemporaryDirectory() as temp:
        filename = os.path.join(temp, "interconnect.sv")
        check_verilog(interconnect, filename)


if __name__ == "__main__":
    from conftest import create_dummy_interconnect_fn
    test_interconnect_codegen(create_dummy_interconnect_fn)
