import os
import struct

from nmigen import *
from n64_board import *
from uart import UART
from ice40_pll import PLL
from wb import WishboneRAM, WishboneUART, WishboneAddressDecoder, Peripheral
from cpu import SERV, PicoRV32
from cart import Cart
from sdram import SDRAMController

class Top(Elaboratable):
    def __init__(self, sys_clk, with_sdram):
        self.sys_clk = sys_clk * 1e6
        self.with_sdram = with_sdram

        self.cart = Cart(sys_clk)
        self.cpu = SERV()
        self.sdram = SDRAMController(self.sys_clk)
        #self.uart = UART(int(self.sys_clk//115200))
        self.buffer = Memory(width=16, depth=256)

        self.wb_uart = WishboneUART(int(self.sys_clk//115200))

    def elaborate(self, platform):
        m = Module()
        
        m.submodules.cart = self.cart
        m.submodules.cpu = self.cpu
        
        if self.with_sdram:
            m.submodules.sdram_ctrl = self.sdram

        with open("irom/irom.bin", "rb") as irom_file:
            irom_init = list(map(lambda a: a[0], struct.iter_unpack("<I",irom_file.read())))

        irom_init += [0xbeeffac0] * (128-len(irom_init))
        irom = WishboneRAM(init=irom_init)
        
        drom_init = [0xbeeffac0] * (128)
        drom = WishboneRAM(init=irom_init)

        decoder = WishboneAddressDecoder(decodes = [
            Peripheral(drom, 0, 128 * 4),
            Peripheral(self.wb_uart, 0x10000000, 0x8)
        ])

        m.submodules.irom = irom
        m.submodules.drom = drom
        m.submodules.wb_uart = self.wb_uart
        m.submodules.decoder = decoder

        m.d.comb += self.cpu.ibus.connect_to(irom.bus)
        m.d.comb += self.cpu.dbus.connect_to(decoder.bus)

        a_counter = Signal(16)
        d_counter = Signal(16)
        data = Signal(16)

        write_happened = Signal()
        read_happened = Signal()

        buffer_r = self.buffer.read_port()
        buffer_w = self.buffer.write_port()
        m.d.comb += buffer_r.addr.eq(a_counter)
        m.d.comb += buffer_w.addr.eq(a_counter)

        m.submodules += buffer_r
        m.submodules += buffer_w

        #m.submodules.uart = uart = self.uart
        """
        m.d.sync += uart.tx_rdy.eq(0)
        m.d.sync += buffer_w.en.eq(0)

        if self.with_sdram:
            m.d.sync += self.sdram.data_out.eq(0xff)

            with m.FSM() as fsm:
                with m.State("wait_sdram"):
                    m.d.sync += self.sdram.cmd.eq(1)
                    with m.If(self.sdram.cmd_ack == 1):
                        m.d.sync += self.sdram.cmd.eq(0)
                        m.next = "write"
                with m.State("write"):
                    with m.If(self.sdram.wr_valid):
                        m.d.sync += write_happened.eq(1)
                        
                        #with m.If(d_counter == 64):
                        #    m.d.sync += d_counter.eq(0)
                        #with m.Else():
                        m.d.sync += d_counter.eq(d_counter+1)
                        m.d.sync += self.sdram.data_out.eq(d_counter)
                        #with m.If(write_happened):
                        #    m.d.sync += a_counter.eq(a_counter+1)
                    with m.Else():
                        with m.If(write_happened):
                            m.d.sync += write_happened.eq(0)
                            m.next = "read_req"
                
                with m.State("wait"):
                    counter = Signal(8)
                    with m.If(counter == 255):
                        m.d.sync += counter.eq(0)
                        m.next = "read_req"
                    with m.Else():
                        m.d.sync += counter.eq(counter+1)
                with m.State("read_req"):
                    m.d.sync += self.sdram.cmd.eq(3)
                    with m.If(self.sdram.cmd_ack == 3):
                        m.d.sync += self.sdram.cmd.eq(0)
                        m.d.sync += a_counter.eq(0)
                        m.next = "read"
                with m.State("read"):
                    with m.If(self.sdram.rd_valid):
                        m.d.sync += read_happened.eq(1)

                        m.d.sync += a_counter.eq(a_counter+1)
                        m.d.sync += buffer_w.en.eq(1)
                        m.d.sync += buffer_w.data.eq(self.sdram.data_in)

                    with m.Elif(read_happened):
                        m.d.sync += read_happened.eq(0)
                        m.d.sync += a_counter.eq(0)
                        m.next = "readout"
                with m.State("readout"):
                    with m.If(a_counter < 0x100):
                        m.d.sync += [
                            uart.tx_data.eq(buffer_r.data[0:8]),
                            uart.tx_rdy.eq(1),
                            a_counter.eq(a_counter+1)
                        ]
                        m.next = "wait_uart"
                    with m.Else():
                        m.next = "done"
                with m.State("wait_uart"):
                    with m.If(uart.tx_ack):
                        m.next = "wait_char_delay"
                with m.State("wait_char_delay"):
                    timer = Signal(16)
                    m.d.sync += timer.eq(timer+1)
                    with m.If(timer > 10000):
                        m.d.sync += timer.eq(0)
                        m.next = "readout"
                with m.State("done"):
                    pass
        return m
        """
        return m

    def ports(self):
        return self.cart.ports()


class CartConcrete(Elaboratable):
    def __init__(self, sys_clk, uart_baud, uart_delay):
        self.sys_clk = sys_clk
        self.uart_baud = uart_baud
        self.uart_delay = uart_delay

    def elaborate(self, platform):
        m = Module()

        n64 = platform.request("n64", xdr={'ad': 1, 'read': 1, 'write': 1, 'ale_l': 1, 'ale_h': 1})

        sdram = platform.request("sdram", xdr = 
            { 
                'clk': 2,
                'clk_en': 1,
                'cs': 1,
                'we': 1,
                'ras': 1,
                'cas': 1,
                'ba': 1,
                'a': 1,
                'dq': 1,
                'dqm': 1
            }
        )

        uart_tx = platform.request("io",6)
        uart_rx = platform.request("io",7)

        top = Top(self.sys_clk, with_sdram=True)
        cart = top.cart

        m.d.comb += [
            uart_tx.oe.eq(1),
            uart_rx.oe.eq(0),
            uart_tx.o.eq(top.wb_uart.uart.tx_o),
            top.wb_uart.uart.rx_i.eq(uart_rx.i)
        ]

        clk = ClockSignal("sync")
        m.d.comb += [
            n64.read.i_clk.eq(clk),
            n64.write.i_clk.eq(clk),
            n64.ale_l.i_clk.eq(clk),
            n64.ale_h.i_clk.eq(clk),
            n64.ad.i_clk.eq(clk),
            n64.ad.o_clk.eq(clk)
        ]

        m.d.comb += [
            sdram.dq.o.eq(cart.n64.ad_o),

            n64.ad.oe.eq(cart.n64.ad_oe),

            cart.n64.ad_i.eq(n64.ad.i),
            cart.n64.read.eq(n64.read.i),
            cart.n64.write.eq(n64.write.i),
            cart.n64.ale_l.eq(n64.ale_l.i),
            cart.n64.ale_h.eq(n64.ale_h.i),
        ]

        clk = ClockSignal()
        sdram_ctrl = top.sdram.sdram
        m.d.comb += [
            sdram.clk.o0.eq(0),
            sdram.clk.o1.eq(1),
            sdram.clk.o_clk.eq(clk),

            sdram.clk_en.o.eq(sdram_ctrl.cke),
            sdram.clk_en.o_clk.eq(clk),

            sdram.cs.o.eq(sdram_ctrl.cs),
            sdram.cs.o_clk.eq(clk),

            sdram.we.o.eq(sdram_ctrl.we),
            sdram.we.o_clk.eq(clk),

            sdram.ras.o.eq(sdram_ctrl.ras),
            sdram.ras.o_clk.eq(clk),

            sdram.cas.o.eq(sdram_ctrl.cas),
            sdram.cas.o_clk.eq(clk),

            sdram.a.o.eq(sdram_ctrl.addr),
            sdram.a.o_clk.eq(clk),

            sdram.ba.o.eq(sdram_ctrl.ba),
            sdram.ba.o_clk.eq(clk),

            sdram.dqm.o.eq(sdram_ctrl.dqm),
            sdram.dqm.o_clk.eq(clk),

            sdram_ctrl.data_in.eq(sdram.dq.i),
            sdram.dq.o.eq(sdram_ctrl.data_out),
            sdram.dq.oe.eq(sdram_ctrl.data_oe),

            sdram.dq.i_clk.eq(clk),
            sdram.dq.o_clk.eq(clk),
        ]

        m.submodules.top = top

        return m

class CartConcretePLL(CartConcrete):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def elaborate(self, platform):
        m = Module()
        clk_pin = ClockSignal("sync")

        pll = PLL(freq_in_mhz=25, freq_out_mhz=self.sys_clk)
        m.domains += pll.domain
        m.submodules += [pll]
        m.d.comb += [
            pll.clk_pin.eq(clk_pin),
        ]
        cap = super().elaborate(platform)
        m.submodules.top = DomainRenamer({'sync': 'pll'})(cap)
        return m

from test import MockN64
class CartSim(Elaboratable):
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        kwargs["with_sdram"] = False

        self.uart_tx = Signal()
        self.uart_rx = Signal()

        self.n64 = MockN64()
        self.top = Top(*self.args, **self.kwargs)

    def elaborate(self, platform):
        m = Module()
        m.submodules.sim_wrapper = self.top

        cart = self.top.cart
        n64 = self.n64

        m.d.comb += [
            n64.ad.o.eq(cart.n64.ad_o),
            n64.ad.oe.eq(cart.n64.ad_oe),

            cart.n64.ad_i.eq(n64.ad.i),
            cart.n64.read.eq(n64.read.i),
            cart.n64.write.eq(n64.write.i),
            cart.n64.ale_l.eq(n64.ale_l.i),
            cart.n64.ale_h.eq(n64.ale_h.i),
        ]
        m.submodules.n64 = n64

        if self.top.with_sdram:
            sdram_io = self.top.sdram.sdram
            m.submodules.sdram_sim = Instance("sdr_wrapper",
                i_dq_in = sdram_io.data_out,
                o_dq_out = sdram_io.data_in,
                i_dq_oe = sdram_io.data_oe,
                i_Addr = sdram_io.addr,
                i_Ba = sdram_io.ba,
                i_Clk = ClockSignal(),
                i_Cke = sdram_io.cke,
                i_Cs_n = ~sdram_io.cs,
                i_Ras_n = ~sdram_io.ras,
                i_Cas_n = ~sdram_io.cas,
                i_We_n = ~sdram_io.we,
                i_Dqm = sdram_io.dqm)

        return m

    def ports(self):
        return []

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        if sys.argv[1] == "generate-top":
            from nmigen.back import rtlil, verilog

            top = Top(sys_clk=0.5)
            print(verilog.convert(top, ports=top.ports(), name="top"))
        if sys.argv[1] == "generate-top-sim":
            from nmigen.back import rtlil, verilog

            top = CartSim(sys_clk=0.5)
            print(verilog.convert(top, ports=top.ports(), name="top"))
        elif sys.argv[1] == "sim":
            cart = CartSim(sys_clk=50)
            n64 = cart.n64

            from nmigen.back import pysim

            sim = pysim.Simulator(cart)
            ports = [n64.ale_h.i, cart.n64.ale_l.i, n64.read.i, n64.write.i, n64.ad.i, n64.ad.o]

            with sim.write_vcd(vcd_file=open("/tmp/cart.vcd", "w"),
                    gtkw_file=open("/tmp/cart.gtkw", "w"),
                    traces=ports):
                sim.add_clock(1/50e6)

                def do_nothing():
                    for i in range(0, 10000):
                        yield

                sim.add_sync_process(do_nothing)
                sim.run()
    else:
        platform = N64Platform()
        concrete = CartConcretePLL(sys_clk = 50, uart_baud = 115200, uart_delay = 10000)
        platform.build(concrete, read_verilog_opts="-I../serv/rtl", do_program=True)