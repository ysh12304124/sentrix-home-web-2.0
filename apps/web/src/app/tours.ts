export interface TourStep {
  selector: string;
  title: string;
  body: string;
}

/** 每个页面就地指到真实控件，而不是再开一个文字说明弹窗。 */
export const TOURS: Readonly<Record<string, readonly TourStep[]>> = {
  memories: [
    {
      selector: ".memory-visual",
      title: "回忆会自动翻页",
      body: "每段停留 8 秒，画面加载完成后才开始计时。鼠标停在画面上、或页面在后台时会暂停。",
    },
    {
      selector: ".memory-caption",
      title: "先看标题和时间地点",
      body: "画面下方只放标题、时间和地点。更完整的描述点“查看完整描述”。",
    },
    {
      selector: ".memory-footer",
      title: "需要时再手动操作",
      body: "“上一段 / 下一段”随时切换；“暂停 / 继续”控制自动翻页。视频要点“播放视频”才加载原片。",
    },
    {
      selector: ".memory-fullscreen-trigger",
      title: "全屏当屏保",
      body: "进入全屏后只显示画面和时间地点，按 Esc 或点右上角退出。",
    },
  ],
  assistant: [
    {
      selector: ".conversation-rail",
      title: "每次对话都留在这里",
      body: "可以新建对话、改名或删除；历史对话的过程也能重新展开看。",
    },
    {
      selector: ".assistant-ask",
      title: "用自己的话提问",
      body: "例如“去年在海边的照片”“孩子生日那天拍了什么”。问题不会出现在地址栏里。",
    },
    {
      selector: ".assistant-log",
      title: "回答会带回相关照片",
      body: "回答下方的“思考过程”用日常语言说明系统做了什么；有部分内容没找到时会直接说明。",
    },
  ],
  timeline: [
    {
      selector: ".timeline-years",
      title: "先选年份",
      body: "这里只列出真的有记录的年份。",
    },
    {
      selector: ".timeline-months",
      title: "再选月份",
      body: "数字是当月的回忆数量。没有记录的月份不会出现。",
    },
    {
      selector: ".timeline-days",
      title: "展开某一天",
      body: "默认收起；点开后才加载当天的卡片，不会一次加载整月。",
    },
  ],
  people: [
    {
      selector: ".family-tabs",
      title: "四个人物视图",
      body: "在人物、关系、待确认的人物和人物介绍之间切换。",
    },
    {
      selector: ".people",
      title: "先确认是谁",
      body: "确认人物后，时间线和回答会更容易用名字找到内容；跳过不会改动原始照片。",
    },
  ],
  library: [
    {
      selector: ".library-summary",
      title: "这里的总数是全部资料",
      body: "数字来自服务器上的完整资料，不是当前这一页已经加载的条数。",
    },
    {
      selector: ".filter-row",
      title: "先筛选或排序",
      body: "按图片、视频、音频筛选；也可以按拍摄时间或导入时间排序。",
    },
    {
      selector: ".asset-grid",
      title: "点开看原始资料",
      body: "卡片下只显示时间和拍摄地点；继续加载不会重复已显示的条目。",
    },
  ],
  imports: [
    {
      selector: ".import-picker",
      title: "把照片和视频加进来",
      body: "点“选择文件”或“选择文件夹”，也可以直接拖进来。",
    },
    {
      selector: ".import-overall",
      title: "看总体进度",
      body: "这里显示全部已接收资料的完成数量，单个文件的状态在右侧列表里。",
    },
    {
      selector: ".import-status",
      title: "上传后继续整理",
      body: "关闭页面也不会中断；稍后回来还能看到同一批进度。",
    },
  ],
};
