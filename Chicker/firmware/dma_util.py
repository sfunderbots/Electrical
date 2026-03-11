import uctypes

DMA_BASE = 0x50000000
PIO0_BASE = 0x50200000
PIO1_BASE = 0x50300000

def sm_dma_put_ring(chan, sm, src, nword, ring_size):
    dma_chan = uctypes.addressof(bytearray(0)) # just to get address math
    chan_base = DMA_BASE + chan * 0x40
    
    if sm < 4:
        pio_base = PIO0_BASE
        TREQ_SEL = sm
    else:
        sm = sm % 4
        pio_base = PIO1_BASE
        TREQ_SEL = sm + 8

    txf_addr = pio_base + 0x10 + sm * 4  # PIO_TXF0 + sm offset

    ctrl = ((1 << 21) |           # IRQ_QUIET
            (TREQ_SEL << 15) |    # data request signal
            (0 << 11) |           # CHAIN_TO = 0
            (0 << 10) |           # RING_SEL = 0 (ring on read)
            (ring_size << 6) |    # RING_SIZE
            (0 << 5) |            # INCR_WRITE = 0
            (1 << 4) |            # INCR_READ = 1
            (2 << 2) |            # DATA_SIZE = 2 (32 bit)
            (1 << 1) |            # HIGH_PRIORITY
            (1 << 0))             # EN

    mem = uctypes.bytearray_at(chan_base, 16)
    import struct
    struct.pack_into("<LLLL", mem, 0,
        uctypes.addressof(src),  # READ_ADDR
        txf_addr,                # WRITE_ADDR
        nword,                   # TRANS_COUNT
        ctrl)                    # CTRL_TRIG — writing this starts the transfer