/**
 * 人物头像。真图走人脸裁剪接口，没有脸退回首字圆形——与 `PeopleView` 的兜底同一形状。
 */

import { faceInstanceCropUrl, type FamilyPerson } from "../../../shared/api/client";
import { membershipOf, personDisplayName } from "./membership";

interface Props {
  readonly person: FamilyPerson;
  readonly size?: "sm" | "md" | "lg";
}

export function PersonAvatar({ person, size = "md" }: Props) {
  const name = personDisplayName(person);
  const faceId = person.avatar_face_instance_id;
  const className = `person-avatar ${size} av-${membershipOf(person)}`;

  if (faceId === null) {
    return (
      <span className={className} data-fallback="initial">
        {name.slice(0, 1)}
      </span>
    );
  }
  return (
    <span className={className}>
      {/* alt 留空：名字在同一张卡片里已经用文字给了，重复一遍对读屏是噪音。 */}
      <img src={faceInstanceCropUrl(faceId)} alt="" loading="lazy" />
    </span>
  );
}
