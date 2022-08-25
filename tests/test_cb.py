from kcanal import CB
from kratos import initial, delay, assert_
from kratos.util import finish, fopen, fscanf, urandom
from kcanal.cyclone import PortNode, SwitchBoxNode, SwitchBoxSide, SwitchBoxIO
from kcanal.tester import Tester
from kcanal.util import write_bitstream, merge_bitstream

import tempfile
import os


def test_cb():
    width = 16
    num_nodes = 10
    config_addr_size = 8
    config_data_size = 32

    port = PortNode("test", 0, 0, width)
    nodes = []
    for i in range(num_nodes):
        sb = SwitchBoxNode(0, 0, i, width, SwitchBoxSide.EAST, SwitchBoxIO.SB_IN)
        sb.add_edge(port)
        nodes.append(sb)

    cb = CB(port, config_addr_size, config_data_size)
    cb.finalize()
    idx = 4
    configs = cb.get_route_bitstream_config(nodes[idx])
    configs = merge_bitstream(configs)
    num_configs = len(configs)

    with tempfile.TemporaryDirectory() as temp:
        bs_filename = os.path.join(temp, "config_data.bs")
        write_bitstream(configs, bs_filename)

        class CBTester(Tester):
            def __init__(self):
                super(CBTester, self).__init__(config_addr_size, config_data_size)
                self.value = self.var("value", 32)
                self.add_dut(cb)
                self.bs_filename = os.path.basename(bs_filename)
                self.num_config = num_configs
                self.add_code(self.test_body)

            @initial
            def test_body(self):
                self.reset()
                self.fd = fopen(self.bs_filename, "r")
                for i in range(self.num_config):
                    self.scanf_read = fscanf(self.fd, "%08h %08h", self.config_addr, self.config_data)
                    self.configure(self.config_addr, self.config_data)

                self.value = urandom() % 0xFFFF
                self.vars.I[idx] = self.value[15, 0]
                delay(0, None)
                assert_(self.vars.O == self.value)

                finish()

        tester = CBTester()

        filename = os.path.join(temp, "test.sv")
        tester.run(filename)


if __name__ == "__main__":
    test_cb()
