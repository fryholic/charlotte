import re

# 1. 영어 단어 검사 (pyenchant 사용)
try:
    import enchant
except ImportError:
    enchant = None


def is_english_sentence(text, threshold=0.7):
    """
    입력 문자열에서 단어들을 추출한 후, pyenchant 영어 사전을 이용하여
    인식된 단어의 비율이 threshold 이상이면 True와 그 비율을 반환합니다.
    """
    if enchant is None:
        raise ImportError("pyenchant 라이브러리가 필요합니다.")
    words = re.findall(r'\b\w+\b', text)
    if not words:
        return False, 0.0
    valid_count = sum(1 for word in words if enchant.Dict("en_US").check(word))
    ratio = valid_count / len(words)
    return (ratio >= threshold), ratio


# 2. 영어 불용어 비율 (nltk 사용)
try:
    import nltk
    from nltk.corpus import stopwords

    nltk.download('stopwords', quiet=True)
except ImportError:
    stopwords = None


def english_stopword_ratio(text):
    """
    입력된 텍스트의 단어 중 nltk의 영어 불용어가 차지하는 비율을 계산합니다.
    일반적인 영어 문장은 일정 수준 이상의 불용어 비율을 보입니다.
    """
    if stopwords is None:
        raise ImportError("nltk 라이브러리와 stopwords 데이터가 필요합니다.")
    english_stopwords = set(stopwords.words('english'))
    words = re.findall(r'\b\w+\b', text.lower())
    if not words:
        return 0.0
    stopword_count = sum(1 for word in words if word in english_stopwords)
    return stopword_count / len(words)


# 3. 언어 감지 (langdetect 사용)
try:
    from langdetect import detect
except ImportError:
    detect = None


def get_langdetect_language(text):
    """
    langdetect 라이브러리를 사용하여 텍스트의 주된 언어를 감지합니다.
    """
    if detect is None:
        return None
    try:
        lang = detect(text)
        return lang
    except Exception:
        return None


# 4. 최종 판별 함수 (여러 신호를 종합)
def detect_text_type(text):
    """
    입력 텍스트에 대해 아래 신호들을 계산한 후,
      - 영어 단어 비율 (pyenchant)
      - 영어 불용어 비율 (nltk)
      - langdetect 결과
    를 종합하여 영어와 한국어 척도를 0~1.0 사이의 값으로 산출하고,
    최종적으로 문자열이 실제 영어 문장인지 또는 영문 알파벳으로 쓴 한국어인지 판별합니다.
    """
    results = {}

    # 신호 1: 영어 단어 비율
    try:
        eng_dict_valid, eng_dict_ratio = is_english_sentence(text)
    except Exception as e:
        eng_dict_valid, eng_dict_ratio = False, 0.0
    results["eng_dict_ratio"] = eng_dict_ratio

    # 신호 2: 영어 불용어 비율
    try:
        eng_stop_ratio = english_stopword_ratio(text)
    except Exception as e:
        eng_stop_ratio = 0.0
    results["eng_stop_ratio"] = eng_stop_ratio

    # 신호 3: langdetect 결과
    detected_lang = get_langdetect_language(text)
    results["langdetect"] = detected_lang

    # 영어 척도 계산
    # 각 신호의 값은 0~1 사이이며, 영어 단어 비율는 그대로, 영어 불용어 비율는 0.5 이상의 값을 1로 정규화,
    # langdetect는 영어이면 1, 아니면 0로 처리.
    normalized_stop = min(eng_stop_ratio / 0.5, 1.0)  # 0.5 이상의 불용어 비율이면 최대 1.0
    score_lang = 1.0 if detected_lang == "en" else 0.0

    # 가중치: 영어 단어 비율 0.4, 불용어 비율 0.3, langdetect 0.3
    english_scale = (0.4 * eng_dict_ratio + 0.3 * normalized_stop + 0.3 * score_lang)
    english_scale = max(0.0, min(english_scale, 1.0))  # 0~1 사이 보정
    results["english_scale"] = english_scale

    # 한국어 척도는 영어 척도의 반대 척도로 가정
    korean_scale = 1.0 - english_scale
    results["korean_scale"] = korean_scale

    # 최종 판별 (영어 척도가 0.5 이상이면 영어 문장으로 판단)
    if english_scale >= 0.5:
        decision = "영어 문장 (실제 영어)"
    else:
        decision = "영문으로 쓴 한국어 (한국어 오타)"
    results["final_decision"] = decision

    return results


# 예제 실행
if __name__ == "__main__":
    test_inputs = [
        "This is a sample sentence.",
        "dkssud",  # QWERTY 입력: 원래는 "안녕" (영문 사전에 없음)
        "rnfr",  # 짧은 입력 예시
        "Hello, how are you doing today?",
        "tjfmf",  # 또 다른 예시
    ]

    for inp in test_inputs:
        detect_text_type(inp)
