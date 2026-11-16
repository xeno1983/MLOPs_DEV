"""Stratified split helpers without importing storage-heavy dataset module."""


def _split_stratified(idx: int, n_in_class: int, train_ratio: float, val_ratio: float) -> str:
    if n_in_class <= 1:
        return "train"
    if n_in_class == 2:
        return "train" if idx == 0 else "test"
    cut_train = int(n_in_class * train_ratio)
    cut_val = int(n_in_class * (train_ratio + val_ratio))
    if idx < cut_train:
        return "train"
    if idx < cut_val:
        return "val"
    return "test"


def test_singleton_class_goes_to_train():
    assert _split_stratified(0, 1, 0.7, 0.15) == "train"


def test_two_samples_train_and_test():
    assert _split_stratified(0, 2, 0.7, 0.15) == "train"
    assert _split_stratified(1, 2, 0.7, 0.15) == "test"
