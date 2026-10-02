"""Captura de tela."""
from __future__ import annotations
import cv2
import numpy as np
import pyautogui


def grab_pil(region=None):
    """Screenshot (PIL RGB). region = [x, y, w, h] ou None para a tela toda."""
    if region is None:
        return pyautogui.screenshot()
    return pyautogui.screenshot(region=tuple(int(v) for v in region))


def grab(region=None) -> np.ndarray:
    """Screenshot como array BGR (formato do OpenCV)."""
    return cv2.cvtColor(np.array(grab_pil(region)), cv2.COLOR_RGB2BGR)
