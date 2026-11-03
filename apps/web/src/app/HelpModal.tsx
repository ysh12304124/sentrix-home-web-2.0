/**
 * 当前页面的就地帮助：这页能完成什么、建议的第一步、结果去哪里看。
 * 不展示端点、并发数、内部状态或已移除的命令面板。
 */

import { Modal } from "../shared/ui/Modal";

interface HelpContent {
  title: string;
  purpose: string;
  first: string;
  result: string;
}

export const HELP_CONTENT: Readonly<Record<string, HelpContent>> = {
  memories: {
    title: "回忆",
    purpose: "自动翻页浏览家里的照片和视频封面，每段只停留一会儿。",
    first: "点“暂停”慢慢看，或用“上一段 / 下一段”手动切换。",
    result: "文字在画面下方；想看更完整的描述，点“查看完整描述”。视频需要点“播放视频”才会加载原片。",
  },
  assistant: {
    title: "提问",
    purpose: "用自己的话问关于家里照片和视频的问题。",
    first: "在输入框里描述你想找的内容，发送后会显示回答依据。",
    result: "回答会附带相关照片；部分内容暂时没找到时，会明确说明是哪一部分。",
  },
  timeline: {
    title: "时间线",
    purpose: "按真实拍摄时间查看有记录的日子。",
    first: "先选年份和月份，再展开某一天。",
    result: "某一天的事件会显示一张代表图和简短说明；点“查看详情”看全部资料。",
  },
  people: {
    title: "人物",
    purpose: "查看照片里出现的人、人物介绍和他们之间的关系。",
    first: "从人物列表打开一个人，确认或补充他的名字。",
    result: "确认后，时间线和问答会更容易用名字找到相关内容。",
  },
  library: {
    title: "资料库",
    purpose: "查看当前相册的全部资料，按类型或时间筛选。",
    first: "选择“图片 / 视频 / 音频”或调整排序。",
    result: "点任意资料可以打开原始文件、拍摄时间和整理状态；继续加载不会重复已显示的条目。",
  },
  imports: {
    title: "添加资料",
    purpose: "把照片和视频加入当前相册。",
    first: "点“选择文件”或“选择文件夹”，也可以直接拖进来。",
    result: "上传成功后会在“添加资料”里继续整理；关闭页面不会中断，稍后回来能看到进度。",
  },
};

export function HelpModal({
  page,
  onClose,
  onOpenOnboarding,
}: {
  page: string;
  onClose: () => void;
  onOpenOnboarding: () => void;
}) {
  const content = HELP_CONTENT[page] ?? HELP_CONTENT.memories;
  return (
    <Modal kicker="如何使用" title={content.title} onClose={onClose}>
      <div className="help-list">
        <div>
          <strong>这页能做什么</strong>
          <span>{content.purpose}</span>
        </div>
        <div>
          <strong>建议的第一步</strong>
          <span>{content.first}</span>
        </div>
        <div>
          <strong>结果在哪里看</strong>
          <span>{content.result}</span>
        </div>
      </div>
      <div className="modal-actions">
        <button type="button" className="button ghost" onClick={onOpenOnboarding}>
          查看全部功能
        </button>
        <button type="button" className="button primary" onClick={onClose}>
          关闭
        </button>
      </div>
    </Modal>
  );
}
