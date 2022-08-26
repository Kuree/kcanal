from kcanal import CB
from kratos import initial, delay, assert_
from kratos.func import task
from kratos.util import finish, fopen, fscanf, urandom, fclose
from kcanal.cyclone import PortNode, SwitchBoxNode, SwitchBoxSide, SwitchBoxIO
from kcanal.tester import Tester
from kcanal.util import write_bitstream, merge_bitstream


import tempfile
import os


def test_cb_data():
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

    with tempfile.TemporaryDirectory() as temp:
        temp = "temp"
        for idx in range(len(nodes)):
            configs = cb.get_route_bitstream_config(nodes[idx])
            configs = merge_bitstream(configs)
            num_configs = len(configs)
            bs_filename = os.path.join(temp, f"config_data{idx}.bs")
            write_bitstream(configs, bs_filename)

        class CBTester(Tester):
            def __init__(self):
                super(CBTester, self).__init__(config_addr_size, config_data_size)
                self.value = self.var("value", 32)
                self.add_dut(cb)
                self.bs_filename = "config_data{0}.bs"
                self.num_config = num_configs
                self.add_code(self.test_body, unroll_for=True)

            @task
            def test_config(self):
                self.reset()
                self.fd = fopen(self.bs_filename.format(idx), "r")
                for i in range(self.num_config):
                    self.scanf_read = fscanf(self.fd, "%08h %08h", self.config_addr, self.config_data)
                    self.configure(self.config_addr, self.config_data)
                fclose(self.fd)

                # test it 42 times
                for i in range(42):
                    self.value = urandom() % 0xFFFF
                    self.vars.I[idx] = self.value[15, 0]
                    delay(1, None)
                    assert_(self.vars.O == self.value)

            @initial
            def test_body(self):
                for i in range(len(nodes)):
                    # overwrite the name and inject local variables
                    self.test_config(idx=i, name=f"test_config{i}")
                finish()

        tester = CBTester()

        filename = os.path.join(temp, "test.sv")
        tester.run(filename)


if __name__ == "__main__":
    test_cb_data()
