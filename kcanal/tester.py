import kratos
import os
import subprocess
from kratos import Generator, posedge, always, initial, negedge, SystemVerilogCodeGenOptions
from kratos.func import task
from kratos.tb import delay
from kratos.util import clock, async_reset


class Tester(Generator):
    # disable pytest collection
    __test__ = False

    def __init__(self, config_addr_width, config_data_width):
        super(Tester, self).__init__("TOP")
        self.clk = self.var("clk", 1)
        self.rst_n = self.var("rst_n", 1)
        self.config_addr = self.var("config_addr", config_addr_width)
        self.config_data = self.var("config_data", config_data_width)
        self.config_en = self.var("config_en", 1)
        self.scanf_read = self.var("scanf_read", 32)

        # read file
        self.fd = self.var("fd", 32)

        @always
        def clk_code():
            delay(5, self.clk.assign(~self.clk))

        @initial
        def clk_initial():
            self.clk = 0

        self.add_code(clk_code)
        self.add_code(clk_initial)

    @task
    def reset(self):
        self.rst_n = 1
        delay(0, None)
        self.rst_n = 0
        delay(0, None)
        self.rst_n = 1
        delay(0, None)

    def add_dut(self, mod: Generator, instance_name: str = "dut", create_var=True, initialize_port=True):
        self.add_child_generator(instance_name, mod, clk=clock(self.clk), config_en=self.config_en,
                                 rst_n=async_reset(self.rst_n),
                                 config_data=self.config_data, config_addr=self.config_addr)
        if create_var:
            variables = []
            for name in mod.ports:
                p = mod.ports[name]
                if name not in self.vars:
                    v = self.var(name, p.width, size=p.size, packed=p.is_packed)
                    self.wire(v, p)
                    variables.append((v, p.port_direction == kratos.PortDirection.In))
            if initialize_port:
                initial_block = self.internal_generator.initial()
                for v, is_in in variables:
                    if is_in:
                        initial_block.add_stmt(v.assign(0))

    @task
    def configure(self, addr, data):
        self.config_en = 1
        self.config_addr = addr
        self.config_data = data
        posedge(self.clk)
        negedge(self.clk)
        self.config_en = 0

    def run(self, filename):
        # disable the usage of unique
        option = SystemVerilogCodeGenOptions()
        option.unique_case = False
        kratos.verilog(self, filename=filename, check_multiple_driver=False, codegen_options=option)
        working_dir = os.path.dirname(filename)
        subprocess.check_call(["iverilog", os.path.basename(filename), "-g2012", "-o", "test"], cwd=working_dir)
        out = subprocess.check_output(["./test"], cwd=working_dir, stderr=subprocess.STDOUT)
        out = out.decode("ascii")
        assert "ERROR" not in out, out
