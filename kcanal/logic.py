from kratos import Generator, always_ff, always_comb, const, posedge, negedge
from kratos.util import clog2
import math
import functools


class OneHotDecoder(Generator):
    def __init__(self, num_case: int):
        name = "Decoder_{0}".format(num_case)
        super().__init__(name)

        self.num_case = num_case
        self.sel_size = clog2(num_case)
        num_sel = clog2(num_case)
        output_size = int(math.pow(2, num_sel))
        self.output_size = output_size

        if self.num_case > self.output_size:
            raise ValueError(
                "output_size {0} cannot be smaller than num_cases {1}".format(
                    output_size, num_case))

        # input
        self.select = self.input("I", self.sel_size)
        self.output = self.output("O", self.output_size)

        # use procedural python code to generate the code
        comb = self.combinational()
        switch = comb.switch_(self.select)
        # adding cases
        for i in range(self.num_case):
            switch.case_(const(i, self.sel_size),
                         self.output(1 << i))
        # add a default case
        switch.case_(None, self.output(0))


class Mux(Generator):
    def __init__(self, height: int, width: int, is_clone: bool = False):
        name = "Mux_{0}".format(height)
        super().__init__(name, is_clone=is_clone)
        self.width = self.param("width", value=width, initial_value=2)

        if height < 1:
            height = 1
        self.height = height

        self.in_ = self.input("I", self.width, size=[height], explicit_array=True)
        self.out_ = self.output("O", self.width)
        self.valid_in = self.input("valid_in", height)
        self.valid_out = self.output("valid_out", 1)
        self.ready_in = self.input("ready_in", 1)
        self.ready_out = self.output("ready_out", height)

        # pass through wires
        if height == 1:
            self.wire(self.out_, self.in_)
            self.wire(self.ready_out, self.ready_in)
            self.wire(self.valid_out, self.valid_in)
            return

        sel_size = clog2(height)
        self.sel = self.input("S", sel_size)

        decoder = OneHotDecoder(height)
        self.sel_out = self.output("sel_out", decoder.output.width)
        self.add_child("decoder", decoder,
                       I=self.sel, O=self.sel_out)

        comb = self.combinational()

        switch_ = comb.switch_(self.sel_out)
        for i in range(height):
            v = 1 << i
            switch_.case_(v, self.out_.assign(self.in_[i]))
            switch_.case_(v, self.valid_out.assign(self.valid_in[i]))
        switch_.case_(None, self.out_.assign(0))
        switch_.case_(None, self.valid_out.assign(0))

        broadcast = [self.ready_in for _ in range(height)]
        self.wire(self.ready_out, kratos.concat(*broadcast))


class ConfigRegister(Generator):
    def __init__(self, width, addr, addr_width, data_width):
        super(ConfigRegister, self).__init__("ConfigRegister")
        self.width = self.param("width", value=width, initial_value=32)
        self.addr_width = self.param("addr_width", value=addr_width, initial_value=8)
        self.data_width = self.param("data_width", value=data_width, initial_value=data_width)
        self.addr = self.param("addr", value=addr, initial_value=0)

        self.config_addr = self.input("config_addr", self.addr_width)
        self.config_data = self.input("config_data", self.data_width)
        self.config_en = self.input("config_en", 1)

        self.clk = self.clock("clk")
        self.rst_n = self.reset("rst_n")

        self.value = self.var("value", width)

        self.in_ = self.input("I", data_width)
        self.out_ = self.output("O", data_width)

        self.enable = self.var("enable", 1)
        self.wire(self.enable, self.config_addr.extend(32) == self.addr)

        self.add_always(self.value_logic)
        self.add_always(self.output_logic)

    @always_ff((posedge, "clk"), (negedge, "rst_n"))
    def value_logic(self):
        if ~self.rst_n:
            self.value = 0
        elif self.config_en and self.enable:
            self.value = self.in_[self.value.width - 1, 0]

    @always_comb
    def output_logic(self):
        if self.enable:
            self.out_ = self.value.extend(self.data_width.value)
        else:
            self.out_ = 0


if __name__ == "__main__":
    import kratos
    mod = Mux(4, 32)
    kratos.verilog(mod, filename="test.sv")
