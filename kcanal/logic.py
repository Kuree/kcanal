from kratos import Generator, always_ff, always_comb, const, posedge, negedge
from kratos.util import clog2
from typing import Dict, List, Tuple
import _kratos
import kratos


class Mux(Generator):
    def __init__(self, height: int, width: int, is_clone: bool = False):
        name = "Mux_{0}".format(height)
        super().__init__(name, is_clone=is_clone)
        self.width = self.param("width", value=width, initial_value=16)

        if height < 1:
            height = 1
        self.height = height

        self.in_ = self.input("I", self.width, size=[height], packed=True)
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

        self.en = self.input("enable", 1)

        sel_size = clog2(height)
        self.sel = self.input("S", sel_size)

        self.sel_out = self.output("sel_out", height)
        self.sel_out_temp = self.var("sel_out_temp", height)
        self.wire(self.sel_out, kratos.ternary(self.en, self.sel_out_temp, 0))
        self.wire(self.out_, kratos.ternary(self.en, self.in_[self.sel], 0))
        self.wire(self.valid_out, kratos.ternary(self.en, self.valid_in[self.sel], 0))
        self.wire(self.ready_out, kratos.ternary(self.en, self.ready_in.duplicate(height), 0))


class ConfigRegister(Generator):
    def __init__(self, width, addr, addr_width):
        super(ConfigRegister, self).__init__(f"ConfigRegister")
        self.width = self.param("width", value=width, initial_value=1)
        self.addr_width = self.param("addr_width", value=addr_width, initial_value=8)
        self.addr = self.param("addr", value=addr, initial_value=0)

        self.config_addr = self.input("config_addr", self.addr_width)
        self.config_data = self.input("config_data", self.width)
        self.config_en = self.input("config_en", 1)

        self.clk = self.clock("clk")
        self.rst_n = self.reset("rst_n")

        self.value = self.output("value", self.width)

        self.enable = self.var("enable", 1)
        self.wire(self.enable, self.config_addr.extend(32) == self.addr)

        self.add_always(self.value_logic)

    @always_ff((posedge, "clk"), (negedge, "rst_n"))
    def value_logic(self):
        if ~self.rst_n:
            self.value = 0
        elif self.config_en and self.enable:
            self.value = self.config_data


def _get_config_reg(width, addr, addr_width) -> ConfigRegister:
    reg = ConfigRegister.clone(width=width, addr=addr, addr_width=addr_width)
    reg.width.value = width
    reg.addr.value = addr
    reg.addr_width.value = addr_width
    return reg


ReadyValidTuple = Tuple[_kratos.Port, _kratos.Port, _kratos.Port]


class ReadyValidGenerator(Generator):
    def __init__(self, name: str, debug: bool = False):
        super(ReadyValidGenerator, self).__init__(name, debug)

    def port_from_def_rv(self, port: _kratos.Port, port_name: str, check_param: bool = True) -> ReadyValidTuple:
        p = self.port_from_def(port, port_name, check_param)
        ready_name = f"{port_name}_ready"
        valid_name = f"{port_name}_valid"
        if port.port_direction == kratos.PortDirection.In:
            v = self.input(valid_name, 1)
            r = self.output(ready_name, 1)
        else:
            v = self.output(valid_name, 1)
            r = self.input(ready_name, 1)
        return p, r, v

    def input_rv(self, port_name, width) -> ReadyValidTuple:
        p = self.input(port_name, width)
        ready_name = f"{port_name}_ready"
        valid_name = f"{port_name}_valid"
        r = self.output(ready_name, 1)
        v = self.input(valid_name, 1)
        return p, r, v

    def output_rv(self, port_name, width) -> Tuple[_kratos.Port, _kratos.Port, _kratos.Port]:
        p = self.output(port_name, width)
        ready_name = f"{port_name}_ready"
        valid_name = f"{port_name}_valid"
        r = self.input(ready_name, 1)
        v = self.output(valid_name, 1)
        return p, r, v

    def wire_rv(self, port1: _kratos.Port, port2: _kratos.Port):
        self.wire(port1, port2)
        port1_gen: _kratos.Generator = port1.generator
        port2_gen: _kratos.Generator = port2.generator
        port1_ready = port1_gen.get_port(f"{port1.name}_ready")
        port2_ready = port2_gen.get_port(f"{port2.name}_ready")
        port1_valid = port1_gen.get_port(f"{port1.name}_valid")
        port2_valid = port2_gen.get_port(f"{port2.name}_valid")
        self.wire(port1_ready, port2_ready)
        self.wire(port1_valid, port2_valid)

    def lift_rv(self, port: _kratos.Port, port_name=None):
        if port_name is None:
            port_name = port.name
        p, r, v = self.port_from_def_rv(port, port_name, check_param=False)
        self.wire(p, port)
        port_ready = port.generator.get_port(f"{port.name}_ready")
        port_valid = port.generator.get_port(f"{port.name}_valid")
        self.wire(r, port_ready)
        self.wire(v, port_valid)

    def lift(self, port: _kratos.Port, port_name):
        p = self.port_from_def(port, port_name, check_param=False)
        self.wire(p, port)
        return p


class Configurable(ReadyValidGenerator):
    def __init__(self, name: str, config_addr_width: int, config_data_width: int, debug: bool = False):
        super(Configurable, self).__init__(name, debug)

        self.config_addr_width = config_addr_width
        self.config_data_width = config_data_width

        self.registers: Dict[str, kratos.Var] = {}
        self.clk = self.clock("clk")
        # reset low
        self.reset = self.reset("rst_n", active_high=False)
        self.config_addr = self.input("config_addr", config_addr_width)
        self.config_data = self.input("config_data", config_data_width)
        self.config_en = self.input("config_en", 1)

        # register map
        # index, low, high
        self.__register_map: Dict[str, Tuple[int, int, int]] = {}

    def add_config(self, name: str, width: int):
        assert name not in self.registers, f"{name} already exists in configuration"
        v = self.var(name, width)
        self.registers[name] = v
        return v

    def finalize(self):
        # instantiate the configuration registers
        # we use greedy bin packing
        regs, reg_map = self.__compute_reg_packing()
        registers: List[ConfigRegister] = []
        for addr, reg_rest in enumerate(regs):
            reg_width = self.config_data_width - reg_rest
            reg = _get_config_reg(reg_width, addr, self.config_addr_width)

            self.add_child_generator(f"config_reg_{addr}", reg, clk=self.clk,
                                     rst_n=self.reset, config_addr=self.config_addr,
                                     config_data=self.config_data[reg_width - 1, 0], config_en=self.config_en)
            registers.append(reg)

        # assign slice
        for name, (idx, start_addr) in reg_map.items():
            v = self.registers[name]
            hi: int = start_addr + v.width - 1
            lo: int = start_addr
            slice_ = registers[idx].value[hi, lo]
            self.wire(v, slice_)
            self.__register_map[name] = (idx, lo, hi)

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
                    reg_map[n] = (idx, self.config_data_width - rest)
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

    def get_config_data(self, name, value) -> Tuple[int, int]:
        idx, lo, hi = self.__register_map[name]
        width = hi - lo + 1
        assert value < (1 << width)
        return idx, value << lo


class FIFO(Generator):
    # based on https://github.com/StanfordAHA/garnet/blob/spVspV/global_buffer/design/fifo.py
    def __init__(self, data_width, depth):

        super().__init__(f"reg_fifo_d_{depth}")

        self.data_width = self.parameter("data_width", 16)
        self.data_width.value = data_width
        self.depth = depth

        # CLK and RST
        self.clk = self.clock("clk")
        self.reset = self.reset("reset")
        self.clk_en = self.clock_en("clk_en", 1)

        # INPUTS
        self.data_in = self.input("I", self.data_width)
        self.data_out = self.output("O", self.data_width)

        self.push = self.input("push", 1)
        self.pop = self.input("pop", 1)
        ptr_width = max(1, clog2(self.depth))

        self._rd_ptr = self.var("rd_ptr", ptr_width)
        self._wr_ptr = self.var("wr_ptr", ptr_width)
        self._read = self.var("read", 1)
        self._write = self.var("write", 1)
        self._reg_array = self.var("reg_array", self.data_width, size=self.depth, packed=True, explicit_array=True)

        self.empty = self.var("empty", 1)
        self.full = self.var("full", 1)

        self.valid_out = self.output("valid_out", 1)
        self.wire(self.valid_out, ~self.empty)
        self.ready_out = self.output("ready_out", 1)
        self.wire(self.ready_out, ~self.full)

        self._num_items = self.var("num_items", clog2(self.depth) + 1)
        self.wire(self.full, self._num_items == self.depth)
        self.wire(self.empty, self._num_items == 0)
        self.wire(self._read, self.pop & ~self.empty)

        self.wire(self._write, self.push & ~self.full)
        self.add_code(self.set_num_items)
        self.add_code(self.reg_array_ff)
        self.add_code(self.wr_ptr_ff)
        self.add_code(self.rd_ptr_ff)
        self.add_code(self.data_out_ff)

    @always_ff((posedge, "clk"), (negedge, "reset"))
    def rd_ptr_ff(self):
        if ~self.reset:
            self._rd_ptr = 0
        elif self._read:
            self._rd_ptr = self._rd_ptr + 1

    @always_ff((posedge, "clk"), (negedge, "reset"))
    def wr_ptr_ff(self):
        if ~self.reset:
            self._wr_ptr = 0
        elif self._write:
            if self._wr_ptr == (self.depth - 1):
                self._wr_ptr = 0
            else:
                self._wr_ptr = self._wr_ptr + 1

    @always_ff((posedge, "clk"), (negedge, "reset"))
    def reg_array_ff(self):
        if ~self.reset:
            self._reg_array = 0
        elif self._write:
            self._reg_array[self._wr_ptr] = self.data_in

    @always_comb
    def data_out_ff(self):
        self.data_out = self._reg_array[self._rd_ptr]

    @always_ff((posedge, "clk"), (negedge, "reset"))
    def set_num_items(self):
        if ~self.reset:
            self._num_items = 0
        elif self._write & ~self._read:
            self._num_items = self._num_items + 1
        elif ~self._write & self._read:
            self._num_items = self._num_items - 1
        else:
            self._num_items = self._num_items


if __name__ == "__main__":
    import kratos

    m = Mux(10, 16)
    kratos.verilog(m, filename="mux.sv")
