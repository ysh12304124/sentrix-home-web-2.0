"""控制服务：局域网登录、按身份转发、反馈、后台与模型准入。

这个包只依赖标准库、FastAPI 与控制库迁移器，不构造产品 ``Container``——
用户媒体、向量和问答正文始终留在各用户 API 的 ``SENTRIX_DATA_DIR`` 下。
"""

__all__ = ["create_app"]


def create_app(*args, **kwargs):  # pragma: no cover - 保持包级导出稳定
    from sentrix.control.main import create_app as _create_app

    return _create_app(*args, **kwargs)
