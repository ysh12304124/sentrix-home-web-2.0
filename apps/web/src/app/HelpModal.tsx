/**
 * 使用帮助。对应 1.0 `src/app.js:1120-1121` 的静态 `help` 模态。
 *
 * 内容按 **2.0 当前真实能力**写，不抄 1.0 里已经不成立的承诺：
 * - 1.0 提到「模型原始 JSON」「WorldMM」，2.0 没有对应读侧；
 * - 事实审核在 2.0 已接上 `GET /facts` + confirm/reject；
 * - 相册新建/删除在 2.0 只有 `GET /memory-spaces`，所以帮助里明说。
 */

import { Modal } from "../shared/ui/Modal";

export function HelpModal({ onClose }: { readonly onClose: () => void }) {
  return (
    <Modal kicker="SENTRIX HOME / HELP" title="当前可用能力" onClose={onClose}>
      <div className="help-list">
        <div>
          <strong>导入</strong>
          <span>
            图片、音频、视频都可以从导入页一次拖入。批量导入每 20 个文件并发提交；
            视频保存后由后台任务读取元数据、提取关键帧并切分场景。进度按任务编号保存在本机，刷新页面后仍可查看和继续；失败时已完成步骤会保留，
            失败后可以点「从已完成步骤继续」。
          </span>
        </div>
        <div>
          <strong>证据</strong>
          <span>
            导入结果是真实 Asset；时间线、资料库和助手回答都能回到原始文件。
            助手交付的照片走结果集授权，只包含它真的看过的图。
          </span>
        </div>
        <div>
          <strong>维护</strong>
          <span>
            冲突事实会以 <code>pending</code> 状态出现在「设备与隐私」页，确认后旧版本
            变为 <code>superseded</code>，驳回不会删除原始证据。
          </span>
        </div>
        <div>
          <strong>命令面板</strong>
          <span>
            顶栏的 ⌘ 按钮可以跳转页面，或把没命中页面的一句话直接交给家庭记忆助手。
          </span>
        </div>
        <div>
          <strong>隐私</strong>
          <span>
            原始文件、人物候选、视频场景与 SQLite 都在当前本机运行；页面没有把媒体
            发往云端。局域网邀请在 2.0 后端尚未提供端点。
          </span>
        </div>
        <div>
          <strong>相册管理</strong>
          <span>
            2.0 的 <code>/memory-spaces</code> 只有 GET，因此目前只能在已有相册之间
            切换，不能新建或删除；侧边栏切换器展示的是后端真实返回的列表。
          </span>
        </div>
      </div>
      <div className="modal-actions">
        <button type="button" className="button primary" onClick={onClose}>关闭</button>
      </div>
    </Modal>
  );
}
