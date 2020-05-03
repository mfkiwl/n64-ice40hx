from nmigen import *
from n64_board import *
from uart import UART
from ice40_pll import PLL
import struct

def to_hex(sig):
    a = []
    for i in range(0, len(sig), 4):
        tab = Array([
            ord('0'),
            ord('1'),
            ord('2'),
            ord('3'),
            ord('4'),
            ord('5'),
            ord('6'),
            ord('7'),
            ord('8'),
            ord('9'),
            ord('a'),
            ord('b'),
            ord('c'),
            ord('d'),
            ord('e'),
            ord('f')
            ])

        if len(sig)-i >= 4:
            a.append(tab[sig[i:i+4]])
        else:
            a.append(tab[sig[i:len(sig)+1]])

    return a[::-1]

class EdgeDetector(Elaboratable):
    def __init__(self, pin):
        self.pin = pin
        self.fall = Signal()
        self.rise = Signal()

    def elaborate(self, platform):
        m = Module()
        last = Signal()
        m.d.sync += [
            last.eq(self.pin),
            self.fall.eq(~self.pin & last),
            self.rise.eq(self.pin & ~last)
        ]

        return m

class Cart(Elaboratable):
    def __init__(self, n64, uart_tx, uart_rx, sys_clk, uart_baud, uart_delay):
        self.uart_tx = uart_tx
        self.uart_rx = uart_rx
        self.n64 = n64
        self.sys_clk = sys_clk * 1e6
        self.uart_baud = uart_baud
        self.uart_delay = uart_delay

    def elaborate(self, platform):
        m = Module()

        clk = ClockSignal("sync")
        m.d.comb += [
            self.n64.read.i_clk.eq(clk),
            self.n64.write.i_clk.eq(clk),
            self.n64.ale_l.i_clk.eq(clk),
            self.n64.ale_h.i_clk.eq(clk),
            self.n64.ad.i_clk.eq(clk),
            self.n64.ad.o_clk.eq(clk)
        ]

        timer = Signal(23)
        m.d.sync += timer.eq(timer+1)

        u = UART(int(self.sys_clk // self.uart_baud))
        m.submodules += u

        m.d.sync += u.tx_rdy.eq(0)

        addr = Signal(32)
        m.submodules.read_edge = read_edge = EdgeDetector(self.n64.read.i)
        
        with m.If(self.n64.ale_l.i):
            with m.If(self.n64.ale_h.i):
                m.d.sync += addr[16:32].eq(self.n64.ad.i)
            with m.Else():
                m.d.sync += addr[0:16].eq(self.n64.ad.i)

        rom_bytes = open("Super Mario 64 (USA).n64", "rb").read()
        rom_bytes = rom_bytes[0:0x1000]
        rom_words = []

        # haha gottem it's byteswapped
        for i in range(0, len(rom_bytes), 2):
            rom_words.append(struct.unpack("H", rom_bytes[i:i+2])[0])

        if rom_words[0] != 0x8037:
            print("Your rom isnt byteswapped plz fix")
            exit()

        rom = Memory(width=16, depth=len(rom_bytes)//2, init=rom_words)
        m.submodules.rom_rd = rom_rd = rom.read_port()
        m.d.comb += rom_rd.addr.eq((addr & 0xffff) >> 1)

        addr_depth = 256
        log_skip_depth = (0x1000 - 0x40)//2 + 2 + 2 + (1024*1024)//2 - 250
        log_skip = Signal(range(0,log_skip_depth+1), reset=log_skip_depth)

        addr_log = Memory(width=32+16, depth=addr_depth)
        addr_log_write_pos = Signal(range(0,addr_depth+1))
        addr_log_read_pos = Signal(range(0,addr_depth))

        m.submodules.addr_log_read = addr_log_read = addr_log.read_port()
        m.submodules.addr_log_write = addr_log_write = addr_log.write_port()
        #m.d.comb += addr_log_read.en.eq(1)
        m.d.sync += addr_log_write.en.eq(0)

        m.d.comb += addr_log_write.addr.eq(addr_log_write_pos)
        m.d.comb += addr_log_read.addr.eq(addr_log_read_pos)

        stb_inc_addr = Signal()
        with m.If(stb_inc_addr):
            # Log addr!
            with m.If(addr_log_write_pos != addr_depth):
                m.d.sync += addr_log_write_pos.eq(addr_log_write_pos+1)

            m.d.sync += stb_inc_addr.eq(0)

        with m.If(read_edge.fall):
            with m.If(log_skip == 0):
                with m.If(addr_log_write_pos != addr_depth):
                    m.d.sync += addr_log_write.data.eq(Cat(addr, rom_rd.data))
                    m.d.sync += addr_log_write.en.eq(1)
                    m.d.sync += stb_inc_addr.eq(1)
            with m.Else():
                m.d.sync += log_skip.eq(log_skip-1)

            m.d.sync += self.n64.ad.oe.eq(1)
            m.d.sync += self.n64.ad.o.eq(rom_rd.data)
            m.d.sync += addr.eq(addr+2)
        with m.Else():
            m.d.sync += self.n64.ad.oe.eq(0)

        initial_chars = Array(map(ord, '\x1b[2J\x1b[H' + '\n' * 10))
        chars = Array([*to_hex(addr_log_read_pos), ord(' '), *to_hex(addr_log_read.data[0:32]), ord(' '), *to_hex(addr_log_read.data[32:48]), ord('\r'), ord('\n')])
        tx_counter = Signal(range(0,max(len(chars),len(initial_chars))))

        wait_full_secs = 1
        wait_full_clks = int(self.sys_clk*wait_full_secs)
        wait_full_timer = Signal(range(0, wait_full_clks+1), reset=wait_full_clks)

        with m.FSM() as fsm:
            with m.State("send_initial_char"):
                m.d.sync += u.tx_data.eq(initial_chars[tx_counter])
                m.d.sync += u.tx_rdy.eq(1)
                m.next = "init_wait_ack"
            with m.State("init_wait_ack"):
                with m.If(u.tx_ack):
                    with m.If(tx_counter == len(initial_chars)-1):
                        m.d.sync += tx_counter.eq(0)
                        m.next = "wait_full"
                    with m.Else():
                        m.d.sync += tx_counter.eq(tx_counter+1)
                        m.next = "initial_wait"
            with m.State("initial_wait"):
                wait_sig = Signal(range(0,self.uart_delay))
                m.d.sync += wait_sig.eq(wait_sig+1)
                with m.If(wait_sig == self.uart_delay-1):
                    m.next = "send_initial_char"

            #######################################

            with m.State("wait_full"):
                m.d.sync += wait_full_timer.eq(wait_full_timer-1)
                with m.If((addr_log_write_pos == addr_depth) | (wait_full_timer == 0)):
                    m.next = "send_char"
            with m.State("send_char"):
                m.d.sync += u.tx_data.eq(chars[tx_counter])
                m.d.sync += u.tx_rdy.eq(1)
                m.next = "wait_ack"
            with m.State("wait_ack"):
                with m.If(u.tx_ack):
                    with m.If(tx_counter == len(chars)-1):
                        m.d.sync += tx_counter.eq(0)
                        m.next = "actual_delay"
                    with m.Else():
                        m.d.sync += tx_counter.eq(tx_counter+1)
                        m.next = "wait"
            with m.State("wait"):
                wait_sig = Signal(range(0,self.uart_delay))
                m.d.sync += wait_sig.eq(wait_sig+1)
                with m.If(wait_sig == self.uart_delay-1):
                    m.next = "send_char"

            with m.State("actual_delay"):
                wait_sig = Signal(range(0,self.uart_delay))
                m.d.sync += wait_sig.eq(wait_sig+1)

                with m.If(wait_sig == self.uart_delay-2):
                    with m.If(addr_log_read_pos != addr_depth-1):
                        m.d.sync += addr_log_read_pos.eq(addr_log_read_pos+1)
                    with m.Else():
                        m.next = "done"
                with m.If(wait_sig == self.uart_delay-1):
                    m.next = "send_char"
            with m.State("done"):
                pass

        m.d.comb += u.rx_i.eq(self.uart_rx)
        m.d.comb += self.uart_tx.eq(u.tx_o)

        return m

class CartConcrete(Elaboratable):
    def __init__(self, sys_clk, uart_baud, uart_delay):
        self.sys_clk = sys_clk
        self.uart_baud = uart_baud
        self.uart_delay = uart_delay

    def elaborate(self, platform):
        m = Module()

        n64 = platform.request("n64", xdr={'ad': 1, 'read': 1, 'write': 1, 'ale_l': 1, 'ale_h': 1})
        uart_tx = platform.request("io",6)
        uart_rx = platform.request("io",7)

        m.d.comb += uart_tx.oe.eq(1)
        m.d.comb += uart_rx.oe.eq(0)

        cap = Cart(n64, uart_tx.o, uart_rx.i, self.sys_clk, self.uart_baud, self.uart_delay)
        m.submodules.cap = cap

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
        m.submodules += DomainRenamer({'sync': 'pll'})(cap)
        return m

class MockIO():
    def __init__(self, name, dir, width=1):
        if dir == "i" or dir == "io":
            self.i = Signal(width, name=name+"_i")
            self.i_clk = Signal(name=name+"_i_clk")

        if dir == "o" or dir == "io":
            self.o = Signal(width, name=name+"_o")
            self.o_clk = Signal(name=name+"_o_clk")

        if dir == "io":
            self.oe = Signal(name=name+"_oe")

class MockN64():
    def __init__(self):
        self.ad = MockIO("n64_data", "io", 16)
        self.ale_h = MockIO("n64_ale_h", "i")
        self.ale_l = MockIO("n64_ale_l", "i")
        self.read = MockIO("n64_read", "i")
        self.write = MockIO("n64_write", "i")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "sim":
        n64 = MockN64()
        uart_tx = Signal()
        uart_rx = Signal()

        mod = Cart(n64, uart_tx, uart_rx, sys_clk=50, uart_baud = 12.5e6, uart_delay = 2)

        from nmigen.back import pysim

        sim = pysim.Simulator(mod)
        ports = [uart_tx, n64.ale_h.i, n64.ale_l.i, n64.read.i, n64.write.i, n64.ad.i, n64.ad.o]

        with sim.write_vcd(vcd_file=open("/tmp/cart.vcd", "w"),
                gtkw_file=open("/tmp/cart.gtkw", "w"),
                traces=ports):
            sim.add_clock(1/50e6)

            def drive_n64():
                yield n64.ale_l.i.eq(0)
                yield n64.ale_h.i.eq(0)
                yield n64.write.i.eq(1)
                yield n64.read.i.eq(1)
                yield n64.ad.i.eq(0)

                def delay(n):
                    for i in range(0,n):
                        yield

                def block_read(addr, n_bytes):
                    #         a    b  c
                    #       <----><--><-->
                    # ale_l       --------
                    # ____________|      |_________
                    #
                    # ale_h ----------
                    # ______|        |_____________
                    # 

                    a = 5
                    b = 10
                    c = 5

                    yield n64.ale_h.i.eq(1)
                    yield n64.ad.i.eq((addr >> 16) & 0xffff)
                    yield from delay(a)
                    yield n64.ale_l.i.eq(1)
                    yield from delay(b-3)
                    yield n64.ale_h.i.eq(0)
                    yield from delay(3)
                    yield n64.ad.i.eq(addr & 0xffff)
                    yield from delay(c)
                    yield n64.ale_l.i.eq(0)

                    yield from delay(100)

                    n = n_bytes//2
                    for i in range(0,n):
                        yield n64.read.i.eq(1)
                        yield from delay(5)
                        yield n64.read.i.eq(0)
                        yield from delay(15)

                yield from block_read(0x10000000, 4)

                for i in range(0x40, 0x1000, 4):
                    yield from block_read(0x10000000 + i, 4)

                for i in range(8, 0x40, 4):
                    yield from block_read(0x10000000 + i, 4)

                #for i in range()

            def do_nothing():
                for i in range(0, 10000):
                    yield
            sim.add_sync_process(do_nothing)
            sim.add_sync_process(drive_n64)
            sim.run()
    else:
        platform = N64Platform()
        platform.build(CartConcretePLL(sys_clk = 50, uart_baud = 115200, uart_delay = 10000), do_program=True)