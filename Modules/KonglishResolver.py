import re

# --- 기본 상수 및 매핑 테이블 -------------------------------------------- #

BASE_CODE = 0xAC00  # '가'

# 2벌식 자판 매핑 (초성, 중성, 종성)
CHO_LIST = [
    "r", "R", "s", "e", "E", "f", "a", "q", "Q", "t",
    "T", "d", "w", "W", "c", "z", "x", "v", "g"
]
JUNG_LIST = [
    "k", "o", "i", "O", "j", "p", "u", "P", "h", "hk",
    "ho", "hl", "y", "n", "nj", "np", "nl", "b", "m", "ml", "l"
]
JONG_LIST = [
    "",  # 0: 종성 없음
    "r",  # 1: ㄱ
    "R",  # 2: ㄲ
    "rt",  # 3: ㄳ
    "s",  # 4: ㄴ
    "sw",  # 5: ㄵ
    "sg",  # 6: ㄶ
    "e",  # 7: ㄷ
    "f",  # 8: ㄹ
    "fr",  # 9: ㄺ
    "fa",  # 10: ㄻ
    "fq",  # 11: ㄼ
    "ft",  # 12: ㄽ
    "fx",  # 13: ㄾ
    "fv",  # 14: ㄿ
    "fg",  # 15: ㅀ
    "a",  # 16: ㅁ
    "q",  # 17: ㅂ
    "qt",  # 18: ㅄ
    "t",  # 19: ㅅ
    "T",  # 20: ㅆ
    "d",  # 21: ㅇ
    "w",  # 22: ㅈ
    "c",  # 23: ㅊ
    "z",  # 24: ㅋ
    "x",  # 25: ㅌ
    "v",  # 26: ㅍ
    "g"  # 27: ㅎ
]

# 초성 단독 자모 (호환 자모)
SINGLE_CHO = [
    '\u3131',  # ㄱ  (index 0, "r")
    '\u3132',  # ㄲ  (1, "R")
    '\u3134',  # ㄴ  (2, "s")
    '\u3137',  # ㄷ  (3, "e")
    '\u3138',  # ㄸ  (4, "E")
    '\u3139',  # ㄹ  (5, "f")
    '\u3141',  # ㅁ  (6, "a")
    '\u3142',  # ㅂ  (7, "q")
    '\u3143',  # ㅃ  (8, "Q")
    '\u3145',  # ㅅ  (9, "t")
    '\u3146',  # ㅆ  (10, "T")
    '\u3147',  # ㅇ  (11, "d")
    '\u3148',  # ㅈ  (12, "w")
    '\u3149',  # ㅉ  (13, "W")
    '\u314A',  # ㅊ  (14, "c")
    '\u314B',  # ㅋ  (15, "z")
    '\u314C',  # ㅌ  (16, "x")
    '\u314D',  # ㅍ  (17, "v")
    '\u314E',  # ㅎ  (18, "g")
]

# 분리형(호환형) 모음 매핑 (대소문자 모두 명시)
ISOLATED_VOWEL = {
    "k": "\u314F",  # ㅏ
    "K": "\u314F",
    "o": "\u3150",  # ㅐ
    "O": "\u3152",  # ㅒ
    "i": "\u3151",  # ㅑ
    "I": "\u3151",
    "j": "\u3153",  # ㅓ
    "J": "\u3153",
    "p": "\u3154",  # ㅔ
    "P": "\u3156",  # ㅖ
    "u": "\u3155",  # ㅕ
    "U": "\u3155",
    "h": "\u3157",  # ㅗ
    "H": "\u3157",
    "hk": "\u3158",  # ㅘ
    "HK": "\u3158",
    "ho": "\u3159",  # ㅙ
    "HO": "\u3159",
    "hl": "\u315A",  # ㅚ
    "HL": "\u315A",
    "y": "\u315B",  # ㅛ
    "Y": "\u315B",
    "n": "\u315C",  # ㅜ
    "N": "\u315C",
    "nj": "\u315D",  # ㅝ
    "NJ": "\u315D",
    "np": "\u315E",  # ㅞ
    "NP": "\u315E",
    "nl": "\u315F",  # ㅟ
    "NL": "\u315F",
    "b": "\u3160",  # ㅠ
    "B": "\u3160",
    "m": "\u3161",  # ㅡ
    "M": "\u3161",
    "ml": "\u3162",  # ㅢ
    "ML": "\u3162",
    "l": "\u3163",  # ㅣ
    "L": "\u3163"
}


# --- 내부 유틸 함수 --------------------------------------------------------- #

def _find_in_list(lst, candidate):
    """
    candidate가 lst에 존재하면 해당 인덱스를 반환.
    없으면 소문자 폴백 후 확인.
    """
    if candidate in lst:
        return lst.index(candidate)
    low_candidate = candidate.lower()
    if low_candidate in lst:
        return lst.index(low_candidate)
    return -1


def _compose_syllable(cho, jung, jong):
    """
    초성, 중성, 종성 인덱스로 완성형 한글 음절을 구성하거나,
    중성이 없으면 단독 초성(호환 자모)를 반환.
    """
    if cho >= 0 and jung < 0:
        return SINGLE_CHO[cho]
    if cho >= 0 and jung >= 0:
        code = BASE_CODE + (cho * 21 + jung) * 28 + jong
        return chr(code)
    return ""


# --- eng_block_to_kor 내 헬퍼: 모음 블록 처리 --------------------------------- #

def process_vowel_block(text, index, result):
    """
    text[index:]에서 2~3글자 복합 모음(또는 단일 모음)을 확인하여,
    ISOLATED_VOWEL에 매핑된 분리형 모음 자모를 결과에 추가한 후
    새로운 인덱스를 반환.
    """
    vowel_str = None
    length = len(text)
    if index + 3 <= length and _find_in_list(JUNG_LIST, text[index:index + 3]) >= 0:
        vowel_str = text[index:index + 3]
        index += 3
    elif index + 2 <= length and _find_in_list(JUNG_LIST, text[index:index + 2]) >= 0:
        vowel_str = text[index:index + 2]
        index += 2
    else:
        vowel_str = text[index]
        index += 1
    if vowel_str in ISOLATED_VOWEL:
        result.append(ISOLATED_VOWEL[vowel_str])
    elif vowel_str.lower() in ISOLATED_VOWEL:
        result.append(ISOLATED_VOWEL[vowel_str.lower()])
    else:
        result.append(vowel_str)
    return index


# --- 메인 변환 함수 --------------------------------------------------------- #

def eng_block_to_kor(eng_text):
    """
    영문 알파벳 문자열을 두벌식 자판 입력으로 보고 한글 음절로 조합.

    [변환 규칙]
      - 대문자는 소문자로 폴백.
      - 초성이 없는 경우, 해당 문자가 모음이면 분리형 모음(호환 자모)으로 직접 출력.
      - 복합 자모(쌍자음, 겹모음, 복합 종성) 모두 처리.
    """
    result = []
    cho = -1  # 초성 인덱스
    jung = -1  # 중성 인덱스
    jong = 0  # 종성 인덱스 (0이면 없음)
    i = 0
    length = len(eng_text)

    def flush_syllable():
        nonlocal cho, jung, jong
        syllable = _compose_syllable(cho, jung, jong)
        if syllable:
            result.append(syllable)
        cho, jung, jong = -1, -1, 0

    while i < length:
        c = eng_text[i]
        ahead2 = eng_text[i:i + 2]
        ahead3 = eng_text[i:i + 3]

        # (A) 알파벳이 아니면 바로 출력
        if not c.isalpha():
            flush_syllable()
            result.append(c)
            i += 1
            continue

        # (B) 초성이 비어있을 때 → 초성 채우기 시도
        if cho < 0:
            idx2 = _find_in_list(CHO_LIST, ahead2) if len(ahead2) >= 2 else -1
            if idx2 >= 0:
                cho = idx2
                i += 2
            else:
                idx1 = _find_in_list(CHO_LIST, c)
                if idx1 >= 0:
                    cho = idx1
                    i += 1
                else:
                    # 초성으로 인식되지 않으면, 모음 여부 확인 후 처리
                    if _find_in_list(JUNG_LIST, c) >= 0:
                        i = process_vowel_block(eng_text, i, result)
                    else:
                        flush_syllable()
                        result.append(c)
                        i += 1
            continue

        # (C) 중성이 비어있다면 → 중성 채우기 시도
        if jung < 0:
            idx3 = _find_in_list(JUNG_LIST, ahead3) if len(ahead3) >= 3 else -1
            idx2 = _find_in_list(JUNG_LIST, ahead2) if len(ahead2) >= 2 else -1
            if idx3 >= 0:
                jung = idx3
                i += 3
            elif idx2 >= 0:
                jung = idx2
                i += 2
            else:
                idx1 = _find_in_list(JUNG_LIST, c)
                if idx1 >= 0:
                    jung = idx1
                    i += 1
                else:
                    flush_syllable()
                    # 재시도: 해당 문자가 초성이면 처리, 아니면 그대로 출력
                    idx_cho = _find_in_list(CHO_LIST, c)
                    if idx_cho >= 0:
                        cho = idx_cho
                    else:
                        result.append(c)
                    i += 1
            continue

        # (D) 초성/중성이 이미 있으므로, 종성 처리 또는 새 음절 시작
        # (D-1) 3글자 복합 종성 확인
        idx_jong3 = _find_in_list(JONG_LIST, ahead3) if len(ahead3) >= 3 else -1
        if idx_jong3 >= 0:
            if jong == 0:
                jong = idx_jong3
                i += 3
            else:
                flush_syllable()
                idx_cho3 = _find_in_list(CHO_LIST, ahead3)
                if idx_cho3 >= 0:
                    cho = idx_cho3
                else:
                    result.append(ahead3)
                i += 3
            continue

        # (D-2) 2글자 복합 종성 확인
        idx_jong2 = _find_in_list(JONG_LIST, ahead2) if len(ahead2) >= 2 else -1
        if idx_jong2 >= 0:
            next_pos = i + 2
            if next_pos < length:
                nxt1 = eng_text[next_pos]
                nxt2 = eng_text[next_pos:next_pos + 2]
                nxt3 = eng_text[next_pos:next_pos + 3]
                if (_find_in_list(JUNG_LIST, nxt3) >= 0 or
                        _find_in_list(JUNG_LIST, nxt2) >= 0 or
                        _find_in_list(JUNG_LIST, nxt1) >= 0):
                    if jong == 0:
                        single_jong = _find_in_list(JONG_LIST, ahead2[0])
                        if single_jong >= 0:
                            jong = single_jong
                        else:
                            flush_syllable()
                            result.append(ahead2[0])
                    else:
                        flush_syllable()
                        idx_cho_0 = _find_in_list(CHO_LIST, ahead2[0])
                        if idx_cho_0 >= 0:
                            cho = idx_cho_0
                        else:
                            result.append(ahead2[0])
                    flush_syllable()
                    idx_cho_1 = _find_in_list(CHO_LIST, ahead2[1])
                    if idx_cho_1 >= 0:
                        cho = idx_cho_1
                    else:
                        result.append(ahead2[1])
                    i += 2
                    continue
                else:
                    if jong == 0:
                        jong = idx_jong2
                    else:
                        flush_syllable()
                        idx_cho_2 = _find_in_list(CHO_LIST, ahead2)
                        if idx_cho_2 >= 0:
                            cho = idx_cho_2
                        else:
                            result.append(ahead2)
                    i += 2
                    continue
            else:
                if jong == 0:
                    jong = idx_jong2
                else:
                    flush_syllable()
                    idx_cho_2 = _find_in_list(CHO_LIST, ahead2)
                    if idx_cho_2 >= 0:
                        cho = idx_cho_2
                    else:
                        result.append(ahead2)
                i += 2
                continue

        # (D-3) 단일 자음 종성 확인
        idx_jong1 = _find_in_list(JONG_LIST, c)
        if idx_jong1 >= 0:
            is_next_vowel = False
            if i + 1 < length:
                nxt1 = eng_text[i + 1]
                nxt2 = eng_text[i + 1:i + 3]
                nxt3 = eng_text[i + 1:i + 4]
                if (_find_in_list(JUNG_LIST, nxt3) >= 0 or
                        _find_in_list(JUNG_LIST, nxt2) >= 0 or
                        _find_in_list(JUNG_LIST, nxt1) >= 0):
                    is_next_vowel = True
            if is_next_vowel:
                flush_syllable()
                idx_cho_c = _find_in_list(CHO_LIST, c)
                if idx_cho_c >= 0:
                    cho = idx_cho_c
                else:
                    result.append(c)
                i += 1
            else:
                if jong == 0:
                    jong = idx_jong1
                else:
                    flush_syllable()
                    idx_cho_c = _find_in_list(CHO_LIST, c)
                    if idx_cho_c >= 0:
                        cho = idx_cho_c
                    else:
                        result.append(c)
                i += 1
            continue

        # (D-4) 종성에도 해당하지 않으면 flush 후 새 음절 시작
        flush_syllable()
        # 여기서 먼저 모음 여부 확인: 초성 후보가 없으면 모음으로 처리
        if _find_in_list(JUNG_LIST, c) >= 0:
            i = process_vowel_block(eng_text, i, result)
        else:
            idx_cho_c = _find_in_list(CHO_LIST, c)
            if idx_cho_c >= 0:
                cho = idx_cho_c
            else:
                result.append(c)
            i += 1

    flush_syllable()
    return "".join(result)


def kor_block_to_eng_upper(kor_text):
    """
    한글(가~힣)을 두벌식 자판 영문으로 분해한 뒤, 대문자로 변환.
    예) "노" → ㄴ+ㅗ → "sh" → "SH"
    """
    res = []
    for ch in kor_text:
        code_val = ord(ch)
        if 0xAC00 <= code_val <= 0xD7A3:
            offset = code_val - BASE_CODE
            cho_idx = offset // (21 * 28)
            jung_idx = (offset % (21 * 28)) // 28
            jong_idx = (offset % (21 * 28)) % 28
            converted = CHO_LIST[cho_idx] + JUNG_LIST[jung_idx]
            if jong_idx != 0:
                converted += JONG_LIST[jong_idx]
            res.append(converted.upper())
        else:
            res.append(ch)
    return "".join(res)


def convert_mixed_string(text):
    """
    문자열 내에서
      - 연속된 영문 알파벳 블록은 한글로 변환 (eng_block_to_kor)
      - 연속된 한글 블록은 영문(대문자)으로 변환 (kor_block_to_eng_upper)
      - 기타 (숫자, 공백, 특수문자)는 그대로 출력
    """

    def is_hangul(ch):
        return 0xAC00 <= ord(ch) <= 0xD7A3

    result = []
    current_block = []
    current_mode = None  # 'eng', 'kor', 또는 None

    def flush_block():
        nonlocal current_block, current_mode
        if not current_block:
            return
        block_str = "".join(current_block)
        if current_mode == 'eng':
            converted = eng_block_to_kor(block_str)
            result.append(converted)
        elif current_mode == 'kor':
            converted = kor_block_to_eng_upper(block_str)
            result.append(converted)
        else:
            result.append(block_str)
        current_block = []
        current_mode = None

    for ch in text:
        if ch.isalpha():
            if is_hangul(ch):
                if current_mode == 'kor':
                    current_block.append(ch)
                else:
                    flush_block()
                    current_block.append(ch)
                    current_mode = 'kor'
            else:
                if current_mode == 'eng':
                    current_block.append(ch)
                else:
                    flush_block()
                    current_block.append(ch)
                    current_mode = 'eng'
        else:
            flush_block()
            result.append(ch)
    flush_block()
    return "".join(result)


def english_ratio_excluding_code_and_urls(text):
    """
    주어진 text에서
      1) 마크다운 코드블록(``` ... ```) 내부 문자 제거
      2) URL(https://..., http://..., www. ...) 제거
      3) 남은 문자 중 영어 알파벳 문자의 비율(0.0~1.0) 계산
    """
    in_code_block = False
    filtered_chars = []
    idx = 0
    length = len(text)
    while idx < length:
        if not in_code_block and text.startswith("```", idx):
            in_code_block = True
            idx += 3
        elif in_code_block and text.startswith("```", idx):
            in_code_block = False
            idx += 3
        else:
            if not in_code_block:
                filtered_chars.append(text[idx])
            idx += 1
    filtered_text = "".join(filtered_chars)
    filtered_text = re.sub(r'https?://\S+', '', filtered_text)
    filtered_text = re.sub(r'www\.\S+', '', filtered_text)
    total_chars = len(filtered_text)
    if total_chars == 0:
        return 0.0
    english_count = sum(ch.isalpha() and ('a' <= ch.lower() <= 'z') for ch in filtered_text)
    return english_count / total_chars
