"""
Convert between US ICAO 24-bit addresses and N-numbers.

The FAA allocates ICAO addresses to US registrations algorithmically, so an
N-number can be derived from the hex code alone -- no database needed. This is
the fallback when an aircraft is not in the registry (recently registered,
recently deregistered, or a registry file that is out of date).

Encoding, for the block A00001 - ADF7C7:

    N + d1 [+ d2 [+ d3 [+ d4 [+ d5]]]] [+ suffix]

    d1      1-9
    d2..d5  0-9
    suffix  "", one letter, or two letters, from a 24-letter alphabet
            that omits I and O (they read as 1 and 0)

Each digit position has a fixed block size. After 1, 2 or 3 digits there are
601 possible tails (empty + 24 + 24*24). After 4 digits only 25 (empty + 24),
because N1234AB would need a sixth character. After 5 digits, only 1.

    block1 = 10*block2 + 601 = 101711
    block2 = 10*block3 + 601 =  10111
    block3 = 10*block4 + 601 =    951
    block4 = 10*1      +  25 =     35

Compatible with Python 3.5+.
"""

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # no I, no O

ICAO_US_FIRST = 0xA00001
ICAO_US_LAST = 0xADF7C7

BLOCK1 = 101711
BLOCK2 = 10111
BLOCK3 = 951
BLOCK4 = 35

# Full suffix space: "" + 24 singles + 576 doubles
SUFFIX_FULL = 601
# Reduced suffix space after four digits: "" + 24 singles
SUFFIX_SHORT = 25


def _suffix_from_index(index, allow_double=True):
    """Index 0 -> "", 1..24 -> single letter, 25..600 -> two letters."""
    if index == 0:
        return ""
    if index <= 24:
        return ALPHABET[index - 1]
    if not allow_double:
        raise ValueError("double-letter suffix not valid at this position")
    rest = index - 25
    return ALPHABET[rest // 24] + ALPHABET[rest % 24]


def _index_from_suffix(suffix):
    if suffix == "":
        return 0
    if len(suffix) == 1:
        return ALPHABET.index(suffix) + 1
    if len(suffix) == 2:
        return 25 + ALPHABET.index(suffix[0]) * 24 + ALPHABET.index(suffix[1])
    raise ValueError("suffix must be 0-2 letters")


def icao_to_n_number(icao):
    """
    Take an ICAO address (int, or hex string like 'a1b2c3') and return the
    N-number, or None if the address is outside the US block.
    """
    if isinstance(icao, str):
        icao = icao.strip().lower().lstrip("~")
        if not icao:
            return None
        try:
            icao = int(icao, 16)
        except ValueError:
            return None

    if not (ICAO_US_FIRST <= icao <= ICAO_US_LAST):
        return None

    offset = icao - ICAO_US_FIRST

    digit1 = offset // BLOCK1 + 1
    offset %= BLOCK1
    out = "N" + str(digit1)
    if offset < SUFFIX_FULL:
        return out + _suffix_from_index(offset)
    offset -= SUFFIX_FULL

    out += str(offset // BLOCK2)
    offset %= BLOCK2
    if offset < SUFFIX_FULL:
        return out + _suffix_from_index(offset)
    offset -= SUFFIX_FULL

    out += str(offset // BLOCK3)
    offset %= BLOCK3
    if offset < SUFFIX_FULL:
        return out + _suffix_from_index(offset)
    offset -= SUFFIX_FULL

    out += str(offset // BLOCK4)
    offset %= BLOCK4
    if offset < SUFFIX_SHORT:
        return out + _suffix_from_index(offset, allow_double=False)
    offset -= SUFFIX_SHORT

    return out + str(offset)


def n_number_to_icao(tail):
    """
    Take an N-number ('N512AB', case-insensitive, leading N optional) and
    return the ICAO address as an int, or None if it is not a valid US tail.
    """
    if not tail:
        return None
    tail = tail.strip().upper()
    if tail.startswith("N"):
        tail = tail[1:]
    if not tail or not tail[0].isdigit() or tail[0] == "0":
        return None

    digits = ""
    suffix = ""
    for char in tail:
        if char.isdigit() and not suffix:
            digits += char
        elif char.isalpha():
            suffix += char
        else:
            return None

    if not 1 <= len(digits) <= 5:
        return None
    if len(suffix) > 2:
        return None
    if len(digits) == 5 and suffix:
        return None
    if len(digits) == 4 and len(suffix) == 2:
        return None
    for char in suffix:
        if char not in ALPHABET:
            return None

    offset = (int(digits[0]) - 1) * BLOCK1
    if len(digits) == 1:
        offset += _index_from_suffix(suffix)
        return ICAO_US_FIRST + offset
    offset += SUFFIX_FULL + int(digits[1]) * BLOCK2

    if len(digits) == 2:
        offset += _index_from_suffix(suffix)
        return ICAO_US_FIRST + offset
    offset += SUFFIX_FULL + int(digits[2]) * BLOCK3

    if len(digits) == 3:
        offset += _index_from_suffix(suffix)
        return ICAO_US_FIRST + offset
    offset += SUFFIX_FULL + int(digits[3]) * BLOCK4

    if len(digits) == 4:
        offset += _index_from_suffix(suffix)
        return ICAO_US_FIRST + offset

    return ICAO_US_FIRST + offset + SUFFIX_SHORT + int(digits[4])


def is_us_address(icao):
    if isinstance(icao, str):
        try:
            icao = int(icao.strip().lower().lstrip("~"), 16)
        except ValueError:
            return False
    return ICAO_US_FIRST <= icao <= ICAO_US_LAST


if __name__ == "__main__":
    # Round-trip the entire US block. Takes a few seconds.
    checked = 0
    for addr in range(ICAO_US_FIRST, ICAO_US_LAST + 1):
        tail = icao_to_n_number(addr)
        back = n_number_to_icao(tail)
        if back != addr:
            raise SystemExit("mismatch at {:06X}: {} -> {}".format(addr, tail, back))
        checked += 1
    print("round-tripped {} addresses, first={} last={}".format(
        checked,
        icao_to_n_number(ICAO_US_FIRST),
        icao_to_n_number(ICAO_US_LAST),
    ))
