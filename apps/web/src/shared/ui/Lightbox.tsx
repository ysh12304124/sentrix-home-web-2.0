/**
 * 全屏看图。**点图片应当放大，不是下载。**
 *
 * 此前时间线详情、助手交付照片、人物证据都把图片包在
 * `<a href="/assets/{id}/file" target="_blank">` 里——新标签打开的是原文件，
 * 浏览器遇到 HEIC 或不认识的 MIME 就直接下载，用户点「图片」得到的是一个文件。
 *
 * 这里只做一件事：给一个 `src`，全屏显示它。关闭方式三个：点遮罩、右上角按钮、Esc。
 * 原文件链接仍然保留在各自的详情里（那是显式的「打开原文件」，语义不同）。
 */
import { useEffect } from "react";

export interface LightboxProps {
  src: string;
  alt?: string;
  caption?: string;
  onClose: () => void;
}

export function Lightbox({ src, alt = "", caption = "", onClose }: LightboxProps) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="lightbox" role="dialog" aria-modal="true" aria-label={alt || "图片预览"} onClick={onClose}>
      <button
        type="button"
        className="lightbox-close"
        aria-label="关闭图片预览"
        onClick={(event) => {
          event.stopPropagation();
          onClose();
        }}
      >
        <span aria-hidden="true">×</span>
        关闭
      </button>
      {/* 点图片本身不该关闭——只有点空白遮罩才关。 */}
      <img src={src} alt={alt} onClick={(event) => event.stopPropagation()} />
      {caption !== "" ? <p className="lightbox-caption">{caption}</p> : null}
    </div>
  );
}
