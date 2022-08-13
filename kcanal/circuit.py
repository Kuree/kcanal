import kratos
import abc

from typing import List
from .cyclone import InterconnectCore


class Core(kratos.Generator, InterconnectCore):
    def __init__(self, name: str, debug: bool = False):
        super(Core, self).__init__(name, debug)

        self.__input_ports: List[kratos.Port] = []
        self.__output_ports: List[kratos.Port] = []

    def input_rv(self, name, width) -> kratos.Port:
        p = self.input(name, width)
        # also add ready valid interface
        self.input(f"{name}_valid", 1)
        self.output(f"{name}_ready", 1)
        self.__input_ports.append(p)
        return p

    def output_rv(self, name, width) -> kratos.Port:
        p = self.output(name, width)
        # also add ready valid interface
        self.output(f"{name}_valid", 1)
        self.input(f"{name}_ready", 1)
        self.__output_ports.append(p)
        return p

    def inputs(self) -> List[kratos.Port]:
        return self.__input_ports

    def outputs(self) -> List[kratos.Port]:
        return self.__output_ports
