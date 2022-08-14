from kratos import Generator, always_ff, always_comb, const, posedge, negedge
from kratos.util import clog2
import math
from typing import Dict, List, Tuple
import functools
import operator


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
        super(ConfigRegister, self).__init__(f"ConfigRegister_{width}")
        self.width = width
        self.addr_width = self.param("addr_width", value=addr_width, initial_value=8)
        self.data_width = self.param("data_width", value=data_width, initial_value=data_width)
        self.addr = self.param("addr", value=addr, initial_value=0)

        self.config_addr = self.input("config_addr", self.addr_width)
        self.config_data = self.input("config_data", self.data_width)
        self.config_en = self.input("config_en", 1)

        self.clk = self.clock("clk")
        self.rst_n = self.reset("rst_n")

        self.value = self.output("value", width)

        self.read_config_data = self.output("read_config_data", data_width)

        self.enable = self.var("enable", 1)
        self.wire(self.enable, self.config_addr.extend(32) == self.addr)

        self.add_always(self.value_logic)
        self.add_always(self.output_logic)

    @always_ff((posedge, "clk"), (negedge, "rst_n"))
    def value_logic(self):
        if ~self.rst_n:
            self.value = 0
        elif self.config_en and self.enable:
            self.value = self.config_data[self.value.width - 1, 0]

    @always_comb
    def output_logic(self):
        if self.enable:
            self.read_config_data = self.value.extend(self.data_width.value)
        else:
            self.read_config_data = 0


class Configurable(Generator):
    def __init__(self, name: str, config_addr_width: int, config_data_width: int, debug: bool = False):
        super(Configurable, self).__init__(name, debug)

        self.config_addr_width = config_addr_width
        self.config_data_width = config_data_width

        self.registers: Dict[str, kratos.Var] = {}
        self.clk = self.clock("clk")
        # reset low
        self.reset = self.reset("rst_n", active_high=False)
        self.read_config_data = self.output("read_config_data", config_data_width)
        self.config_addr = self.input("config_addr", config_addr_width)
        self.config_data = self.input("config_data", config_addr_width)
        self.config_en = self.input("config_en", 1)

    def add_config(self, name: str, width: int):
        assert name not in self.registers, f"{name} already exists in configuration"
        v = self.var(name, width)
        self.registers[name] = v

    def finalize(self):
        # instantiate the configuration registers
        # we use greedy bin packing
        regs, reg_map = self.__compute_reg_packing()
        registers: List[ConfigRegister] = []
        for addr, reg_rest in enumerate(regs):
            reg_width = self.config_data_width - reg_rest
            print("reg_width", reg_width)
            reg = ConfigRegister(reg_width, addr, self.config_addr_width, self.config_data_width)

            self.add_child_generator(f"config_reg_{addr}", reg, clk=self.clk,
                                     rst_n=self.reset, config_addr=self.config_addr,
                                     config_data=self.config_data, config_en=self.config_en)
            registers.append(reg)

        # assign slice
        for name, (idx, start_addr) in reg_map.items():
            v = self.registers[name]
            hi: int = start_addr + v.width - 1
            lo: int = start_addr
            slice_ = registers[idx].value[hi, lo]
            self.wire(v, slice_)
        # OR all the read config data
        read_data_ors = [reg.read_config_data for reg in registers]
        read_data_value = functools.reduce(operator.or_, read_data_ors)
        self.wire(self.read_config_data, read_data_value)

    def __compute_reg_packing(self):
        # greedy bin packing
        regs: List[int] = []
        reg_map: Dict[str, Tuple[int, int]] = {}

        def place(n: str, var: kratos.Var):
            res = False
            w = var.width
            assert w <= self.config_data_width
            for idx, rest in enumerate(regs):
                if rest >= w:
                    # place it
                    reg_map[n] = (idx, rest)
                    regs[idx] = rest - w
                    res = True
                    break
            if not res:
                # place a new one
                rest = self.config_data_width - w
                reg_map[n] = (len(regs), 0)
                regs.append(rest)

        # to ensure deterministic behavior, names are sorted first
        names = list(self.registers.keys())
        names.sort()
        for name in names:
            v = self.registers[name]
            place(name, v)

        return regs, reg_map


if __name__ == "__main__":
    import kratos
    config = Configurable("Core", 32, 32, debug=True)
    config.add_config("test1", 16)
    config.add_config("test3", 16)
    config.add_config("test2", 20)
    config.finalize()
    kratos.verilog(config, filename="test.sv")
