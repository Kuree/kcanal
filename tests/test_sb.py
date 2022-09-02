from kcanal.tester import Tester
from kcanal.circuit import SB, create_name
from kcanal.util import DisjointSwitchBox, SwitchBoxSide, SwitchBoxIO, merge_bitstream, write_bitstream
from kratos import initial
from kratos.util import fopen, fscanf, fclose, urandom, finish
from kratos.tb import delay, assert_

import os
import tempfile


def test_sb_no_fifo():
    addr_width = 8
    data_width = 32
    switchbox = DisjointSwitchBox(0, 0, 5, 16)
    sb = SB(switchbox, addr_width, data_width, "Test")
    sb.finalize()

    src_node = switchbox.get_sb(SwitchBoxSide.WEST, 0, SwitchBoxIO.SB_IN)
    dst_node = switchbox.get_sb(SwitchBoxSide.EAST, 0, SwitchBoxIO.SB_OUT)
    configs = sb.get_route_bitstream_config(src_node, dst_node)
    configs = merge_bitstream(configs)
    num_configs = len(configs)

    with tempfile.TemporaryDirectory() as temp:
        class SBTester(Tester):
            def __init__(self):
                super(SBTester, self).__init__(addr_width, data_width)
                self.value = self.var("value", 32)
                self.add_dut(sb)
                self.bs_filename = "config_data.bs"
                self.num_config = num_configs
                self.add_code(self.test_config, unroll_for=True)
                bs_filename = os.path.join(temp, f"config_data.bs")
                write_bitstream(configs, bs_filename)

            @initial
            def test_config(self):
                self.reset()
                self.fd = fopen(self.bs_filename, "r")
                for i in range(self.num_config):
                    self.scanf_read = fscanf(self.fd, "%08h %08h", self.config_addr, self.config_data)
                    self.configure(self.config_addr, self.config_data)
                fclose(self.fd)

                # test it 42 times
                for i in range(42):
                    self.value = urandom() % 0xFFFF
                    self.vars[create_name(str(src_node))] = self.value[15, 0]
                    delay(1, None)
                    assert_(self.vars[create_name(str(dst_node))] == self.value)

                finish()

        tester = SBTester()

        filename = os.path.join(temp, "test.sv")
        tester.run(filename)


if __name__ == "__main__":
    test_sb_no_fifo()
