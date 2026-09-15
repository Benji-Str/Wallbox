import struct, sys
sys.path.insert(0, __file__.rsplit('/',2)[0])
from meter.mid import decode, words_needed

def to_regs(fmt, val):
    raw = struct.pack(fmt, val)
    return [struct.unpack(">H", raw[i:i+2])[0] for i in range(0, len(raw), 2)]

# float32, normale Wortreihenfolge
r = to_regs(">f", 1234.5)
print("f32 big   :", decode(r, "f32"), "aus", r); assert abs(decode(r,"f32")-1234.5) < 0.01
# vertauschte Wortreihenfolge (haeufigste Fehlerquelle)
print("f32 little:", decode(r[::-1], "f32", "little")); assert abs(decode(r[::-1],"f32","little")-1234.5) < 0.01
# Falsche Reihenfolge -> Unsinn, genau das soll man erkennen
falsch = decode(r[::-1], "f32", "big")
print("f32 falsch:", falsch, "(absichtlich Unsinn)"); assert abs(falsch-1234.5) > 1

# u32 mit Skalierung 0,1 (Finder)
r = to_regs(">I", 45678)
print("u32*0.1   :", decode(r, "u32", "big", 0.1)); assert abs(decode(r,"u32","big",0.1)-4567.8) < 0.01
# u64 mit 0,01 (ABB)
r = to_regs(">Q", 123456)
print("u64*0.01  :", decode(r, "u64", "big", 0.01)); assert abs(decode(r,"u64","big",0.01)-1234.56) < 0.01
# i32 negativ (Rueckspeisung)
r = to_regs(">i", -3500)
print("i32 neg   :", decode(r, "i32")); assert decode(r,"i32") == -3500.0

assert (words_needed("f32"), words_needed("u64"), words_needed("i16")) == (2,4,1)
print("\nALLE TESTS OK")
