import { Modal } from "../../shared/ui/Modal";
import { useTheme } from "./ThemeProvider";
import { THEMES, themeLabel } from "./themes";

export function ThemePicker({ onClose, onFeedback }: { onClose: () => void; onFeedback?: () => void }) {
  const { theme, selectTheme, storageError } = useTheme();
  return (
    <Modal kicker="给回忆换一种心情" title="界面风格" onClose={onClose}>
      <p className="theme-intro">选择喜欢的外观，当前页面会立即换上新风格。</p>
      <div className="theme-options" role="group" aria-label="选择界面风格">
        {THEMES.map((item) => (
          <button key={item.id} type="button" className="theme-choice" data-preview-theme={item.id}
            aria-pressed={theme === item.id} onClick={() => selectTheme(item.id)}>
            <span className="theme-preview" aria-hidden="true">
              <span className="theme-preview-nav"><i /><i /><i /></span>
              <span className="theme-preview-content"><span className="theme-preview-photo" /><span className="theme-preview-copy"><i /><i /><b /></span></span>
            </span>
            <span className="theme-choice-heading"><strong>{item.code} · {item.name}</strong><span className="theme-selected">{theme === item.id ? "✓ 正在使用" : "选择"}</span></span>
            <span className="theme-choice-description">{item.description}</span>
          </button>
        ))}
      </div>
      <p className="theme-save-status" role="status">{storageError ? "已切换，本机暂无法保存。" : `正在使用 ${themeLabel(theme)}。外观设置保存在此浏览器。`}</p>
      {onFeedback ? <div className="theme-footer"><span>哪一种更适合你？</span><button type="button" className="button ghost" onClick={onFeedback}>反馈界面感受</button></div> : null}
    </Modal>
  );
}
