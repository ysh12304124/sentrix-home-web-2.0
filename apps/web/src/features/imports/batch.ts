/**
 * 分片并发。
 *
 * 1.0 `src/app.js:1565-1577` 在**服务端提供批量导入端点**时按 `chunkSize = 20` 分片；
 * 2.0 的单文件统一入口是 `POST /import`，所以「每批 20 个并发提交」由前端自己分片。
 *
 * **并发上限是每片 20，不是整个列表 20。** 写成「一次性 `Promise.all` 全量」在
 * 一个相册文件夹（几百上千个文件）上会同时开几百个 multipart 请求，浏览器连接池
 * 打满后反而更慢，失败面也更大；写成串行则是 2.0 原来的实现，导入一张等一张。
 *
 * 返回顺序与输入一致（`Promise.all` 保序 + 按下标写回），失败与否由 `worker` 自己
 * 转成结果，这里不做全或无——一个坏文件不该让同批其余文件的结果丢掉。
 */

export const IMPORT_BATCH_SIZE = 20;

export async function runInBatches<T, R>(
  items: readonly T[],
  batchSize: number,
  worker: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  if (batchSize < 1) throw new RangeError("batchSize 必须 >= 1");
  const results: R[] = new Array(items.length);
  for (let offset = 0; offset < items.length; offset += batchSize) {
    const chunk = items.slice(offset, offset + batchSize);
    const settled = await Promise.all(
      chunk.map((item, index) => worker(item, offset + index)),
    );
    settled.forEach((result, index) => {
      results[offset + index] = result;
    });
  }
  return results;
}
