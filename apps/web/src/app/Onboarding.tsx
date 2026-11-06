import { useState } from "react";

import { Modal } from "../shared/ui/Modal";

const SCREENS = [
  {
    title: "你可以在这里做什么",
    body: (
      <div className="onboarding-list">
        <div><strong>回忆</strong><span>每天挑几段值得重看的画面。</span></div>
        <div><strong>提问</strong><span>用自己的话问，回答会带回相关照片。</span></div>
        <div><strong>时间线</strong><span>先选年份和月份，再展开某一天。</span></div>
        <div><strong>人物</strong><span>看看照片里的人，以及他们之间的关系。</span></div>
        <div><strong>资料库</strong><span>筛选并打开每一份原始资料。</span></div>
        <div><strong>添加资料</strong><span>把照片和视频交给家里慢慢整理。</span></div>
      </div>
    ),
  },
  {
    title: "先添加，再等待整理",
    body: (
      <div className="onboarding-copy">
        <p>你可以一次添加照片和视频，也可以选择整个文件夹。</p>
        <p>上传后会自动继续整理，进度可以在“添加资料”里查看。</p>
        <p>还没整理完时，部分回忆和回答暂时不可用；整理完成后会自动出现。</p>
      </div>
    ),
  },
  {
    title: "从回忆继续探索",
    body: (
      <div className="onboarding-copy">
        <p>已有事件可以在回忆页慢慢看；想找具体内容时，就去提问页用自己的话问。</p>
        <p>人物、时间线和资料库用来继续查看；还没有事件时，“看看回忆”会给出真实的空状态。</p>
      </div>
    ),
  },
] as const;

export function Onboarding({
  onSkip,
  onComplete,
  onNavigate,
}: {
  onSkip: () => void;
  onComplete: () => void;
  onNavigate: (path: string) => void;
}) {
  const [step, setStep] = useState(0);
  const screen = SCREENS[step];
  const last = step === SCREENS.length - 1;

  return (
    <Modal kicker="第一次使用" title={screen.title} onClose={onSkip}>
      <div className="onboarding-body">{screen.body}</div>
      <div className="onboarding-progress" aria-label={`第 ${step + 1} 屏，共 ${SCREENS.length} 屏`}>
        {SCREENS.map((_, index) => (
          <span key={index} data-active={index === step} />
        ))}
      </div>
      <div className="onboarding-actions">
        <button type="button" className="text-button" onClick={onSkip}>跳过</button>
        <span className="onboarding-spacer" />
        {step > 0 ? (
          <button type="button" className="button ghost" onClick={() => setStep(step - 1)}>
            上一步
          </button>
        ) : null}
        {last ? (
          <>
            <button
              type="button"
              className="button ghost"
              onClick={() => {
                onComplete();
                onNavigate("/imports");
              }}
            >
              去添加资料
            </button>
            <button
              type="button"
              className="button primary"
              onClick={() => {
                onComplete();
                onNavigate("/");
              }}
            >
              看看回忆
            </button>
          </>
        ) : (
          <button type="button" className="button primary" onClick={() => setStep(step + 1)}>
            下一步
          </button>
        )}
      </div>
    </Modal>
  );
}
