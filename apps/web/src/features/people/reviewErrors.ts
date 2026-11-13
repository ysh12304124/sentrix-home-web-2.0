import { ApiError } from "../../shared/api/client";

/**
 * 后端错误 → 用户能读的中文。**按 ``code`` 分支，不按状态码猜**——``code`` 才是
 * ``api/errors.py`` 契约里的字段。
 *
 * 四个码各自对应一类**用户能采取的行动**，所以四句话不一样：请求不成立要用户改输入，
 * 找不到要刷新，冲突要重新看一眼现在的状态，无法处理是这张脸本身的问题。
 * 兜底那句带上 HTTP 状态码与 ``detail``，因为没归类的错误只能靠原始信息定位。
 */
export function describeReviewError(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return error instanceof Error ? `请求失败：${error.message}` : "请求失败：未知错误";
  }
  switch (error.code) {
    case "invalid_request":
      return `请求不成立：${error.message}。请改一下填写的内容再试。`;
    case "not_found":
      return `服务端找不到这个对象（${error.message}），可能已被删除或合并。请刷新后重试。`;
    case "conflict":
      return `当前状态不允许这一步：${error.message}。请刷新后再决定怎么处理。`;
    case "unprocessable":
      return `服务端处理不了这份数据：${error.message}`;
    default:
      return `请求失败（HTTP ${error.status}）：${error.detail || error.message}`;
  }
}

/** 未确认人物的统一用户文案。内部 cluster id 永远不显示。 */
export const UNNAMED_PERSON_NAME = "未确认姓名人物";

const INTERNAL_CLUSTER_PREFIX = "待确认人物簇";

/**
 * 人物显示名。空值与落库的内部名都收成同一句用户文案。
 *
 * 后端出口已经会替换 pending 实体，这里再兜一层：旧数据、缓存响应或某个漏改的
 * 端点把「待确认人物簇 · cluster-x」带上来时，界面也不该露出内部 id。
 */
export function publicPersonName(value: string | null | undefined): string {
  const name = (value ?? "").trim();
  if (name === "" || name.startsWith(INTERNAL_CLUSTER_PREFIX)) return UNNAMED_PERSON_NAME;
  return name;
}

/** 簇的显示名。只做展示兜底，不从 ``status`` / ``entity_status`` 反推服务端判断。 */
export function clusterDisplayName(cluster: { canonical_name: string | null }): string {
  return publicPersonName(cluster.canonical_name);
}
