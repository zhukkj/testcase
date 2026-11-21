"""生成结果去重

背景：LLM 一次生成十几二十条用例时，经常出现 2~3 条语义重复
（措辞不同但测的是同一件事），这些用例进了用例库就是噪音。

做法：把用例描述批量向量化（一次 encode，不要一条条算），
两两算余弦相似度，超过阈值的只保留分数更高/更靠前的那条。
"""

from typing import Dict, List
from apps.utils.logger_manager import get_logger

logger = get_logger(__name__)


def dedup_test_cases(test_cases: List[Dict], embedder,
                     threshold: float = 0.9,
                     key: str = 'description') -> Dict:
    """按语义相似度去重

    Args:
        test_cases: 用例列表，每项含 description
        embedder: 需实现 get_embeddings(list[str]) -> list[list[float]]
        threshold: 相似度阈值，>= 该值视为重复（0.9 比较保守，不会误杀）
        key: 用于比较的字段

    Returns:
        {"test_cases": 去重后列表, "removed": 被删掉的条数}
    """
    if not test_cases or len(test_cases) < 2:
        return {"test_cases": test_cases or [], "removed": 0}

    texts = [(tc.get(key) or '').strip() for tc in test_cases]
    if not any(texts):
        return {"test_cases": test_cases, "removed": 0}

    try:
        embeddings = embedder.get_embeddings(texts, show_progress_bar=False)
        matrix = [list(e) for e in embeddings]
    except Exception as e:
        logger.warning(f"去重时向量化失败，跳过去重: {e}")
        return {"test_cases": test_cases, "removed": 0}

    import numpy as np
    arr = np.asarray(matrix, dtype='float32')
    # 归一化后点积 = 余弦相似度
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1e-9
    arr = arr / norms
    sim = arr @ arr.T

    keep_flags = [True] * len(test_cases)
    dropped = 0
    for i in range(len(test_cases)):
        if not keep_flags[i]:
            continue
        for j in range(i + 1, len(test_cases)):
            if keep_flags[j] and sim[i][j] >= threshold:
                keep_flags[j] = False
                dropped += 1
                logger.info(
                    f"去重: 第 {j + 1} 条与第 {i + 1} 条相似度 {sim[i][j]:.3f}，已合并"
                )

    kept = [tc for tc, keep in zip(test_cases, keep_flags) if keep]
    if dropped:
        logger.info(f"去重完成：{len(test_cases)} 条 → {len(kept)} 条，合并 {dropped} 条")
    return {"test_cases": kept, "removed": dropped}
