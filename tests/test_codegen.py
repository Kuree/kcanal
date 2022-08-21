import subprocess

from kcanal.circuit import CB, SB, TileCircuit
from kcanal.util import DummyCore
from kcanal.cyclone import PortNode, Node, ImranSwitchBox, DisjointSwitchBox, Tile, SwitchBoxSide, SwitchBoxIO, \
    SBConnectionType

import kratos
import shutil
import tempfile
import pytest
import os

iverilog_available = shutil.which("iverilog") is not None


def check_verilog(filename):
    subprocess.check_call(["iverilog", os.path.basename(filename), "-g2012"], cwd=os.path.dirname(filename))


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
def test_sb_codegen():
    switchbox = ImranSwitchBox(0, 0, 2, 1)
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
    kratos.set_global_debug(True)
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
        temp = "temp"
        filename = os.path.join(temp, "tile.sv")
        kratos.verilog(tile_circuit, filename=filename, optimize_passthrough=False)


if __name__ == "__main__":
    test_tile_codegen()
