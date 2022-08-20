import subprocess

from kcanal.circuit import CB, SB
from kcanal.cyclone import PortNode, Node, ImranSwitchBox

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
        temp = "temp"
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


if __name__ == "__main__":
    test_cb_codegen()
