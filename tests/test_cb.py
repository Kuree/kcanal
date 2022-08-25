from kcanal import CB
from kratos import initial, assert_, posedge, always, Generator, delay, verilog
from kratos.util import urandom, clock, async_reset, finish
from kcanal.cyclone import PortNode, SwitchBoxNode, SwitchBoxSide, SwitchBoxIO

import tempfile
import os


def test_cb():
    width = 16
    num_nodes = 10
    top = Generator("TOP")
    clk = top.var("clk", 1)
    rst_n = top.var("rst_n", 1)
    config_addr = top.var("config_addr", 8)
    config_data = top.var("config_data", 32)
    config_en = top.var("config_en", 1)
    in_ = top.var("in", width, size=num_nodes, packed=True)
    ready_in = top.var("ready_in", 1)
    valid_in = top.var("valid_in", num_nodes)
    ready_out = top.var("ready_out", num_nodes)
    valid_out = top.var("valid_out", 1)

    @always
    def clk_code():
        delay(5, clk.assign(~clk))

    @initial
    def clk_initial():
        clk = 0

    top.add_code(clk_code)
    top.add_code(clk_initial)

    port = PortNode("test", 0, 0, width)
    for i in range(num_nodes):
        sb = SwitchBoxNode(0, 0, i, width, SwitchBoxSide.EAST, SwitchBoxIO.SB_IN)
        sb.add_edge(port)
    cb = CB(port, 8, 32)
    top.add_child("cb", cb, clk=clock(clk), rst_n=async_reset(rst_n), config_data=config_data, config_addr=config_addr,
                  config_en=config_en, I=in_, ready_in=ready_in, ready_out=ready_out, valid_in=valid_in,
                  valid_out=valid_out)

    # compute configuration
    @initial
    def test_body():
        finish()

    top.add_code(test_body)

    with tempfile.TemporaryDirectory() as temp:
        filename = os.path.join(temp, "test.sv")
        verilog(top, filename=filename, check_multiple_driver=False)


if __name__ == "__main__":
    test_cb()
