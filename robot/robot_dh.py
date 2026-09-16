import roboticstoolbox as rtb
import numpy as np
from spatialmath import SE3


def create_humaniod_robot():
    """Create the 20-axis humanoid kinematic tree described in the joint table.

    The supplied joint axes are expressed in the ``base`` frame at the
    mechanical zero pose.  This model therefore uses the following explicit
    convention: every link frame is parallel to ``base`` at zero pose, and
    each ``xyz_mm`` value is the fixed offset from the parent-link frame to
    the joint origin.  When a parent moves, the offset and all descendant
    axes move with it as a rigid body.

    The robot has three leaf links: ``l6`` (head), ``l13`` (left arm), and
    ``l20`` (right arm).  No joint limits were supplied, so the teaching UI
    uses the Robotics Toolbox default slider range of [-pi, pi] for every
    joint.
    """

    # (joint name, parent link name, xyz offset in mm, axis at zero pose)
    joint_specs = (
        ("j1", None, (0.0, 0.0, 350.0), (0, -1, 0)),
        ("j2", "l1", (0.0, 0.0, 300.0), (0, -1, 0)),
        ("j3", "l2", (0.0, 0.0, 300.0), (0, -1, 0)),
        ("j4", "l3", (0.0, 0.0, 250.950), (0, 0, 1)),
        ("j5", "l4", (0.0, 0.0, 272.950), (0, 0, 1)),
        ("j6", "l5", (0.0, 0.0, 116.70), (0, -1, 0)),
        ("j7", "l4", (0.0, 75.0, 294.350), (0, 1, 0)),
        ("j8", "l7", (0.0, 165.0, 0.0), (1, 0, 0)),
        ("j9", "l8", (0.0, 198.3, 0.0), (0, 1, 0)),
        ("j10", "l9", (0.0, 94.7, 0.0), (1, 0, 0)),
        ("j11", "l10", (0.0, 128.5, 0.0), (0, 1, 0)),
        ("j12", "l11", (0.0, 58.5, 0.0), (1, 0, 0)),
        ("j13", "l12", (0.0, 128.5, 0.0), (0, 1, 0)),
        ("j14", "l4", (0.0, -75.0, 294.350), (0, -1, 0)),
        ("j15", "l14", (0.0, -165.0, 0.0), (1, 0, 0)),
        ("j16", "l15", (0.0, -198.3, 0.0), (0, -1, 0)),
        ("j17", "l16", (0.0, -94.7, 0.0), (1, 0, 0)),
        ("j18", "l17", (0.0, -128.5, 0.0), (0, -1, 0)),
        ("j19", "l18", (0.0, -58.5, 0.0), (1, 0, 0)),
        # Kept as supplied: unlike j14/j16/j18, j20 has a +Y axis.
        ("j20", "l19", (0.0, -128.5, 0.0), (0, 1, 0)),
    )

    links_by_name = {}
    links = []
    for joint_name, parent_name, xyz_mm, axis in joint_specs:
        x, y, z = (value * 0.001 for value in xyz_mm)
        ets = rtb.ET.tx(x) * rtb.ET.ty(y) * rtb.ET.tz(z)
        ets *= _revolute_et(axis)

        link_name = f"l{joint_name[1:]}"
        parent = links_by_name.get(parent_name) if parent_name is not None else None
        link = rtb.ELink(ets, name=link_name, parent=parent)
        links_by_name[link_name] = link
        links.append(link)

    return rtb.ERobot(links, name="kaanh_humanoid")


def _revolute_et(axis):
    """Return an elementary revolute transform for an axis-aligned unit axis."""

    axis = tuple(axis)
    transforms = {
        (1, 0, 0): rtb.ET.Rx,
        (-1, 0, 0): rtb.ET.Rx,
        (0, 1, 0): rtb.ET.Ry,
        (0, -1, 0): rtb.ET.Ry,
        (0, 0, 1): rtb.ET.Rz,
        (0, 0, -1): rtb.ET.Rz,
    }
    try:
        return transforms[axis](flip=any(value < 0 for value in axis))
    except KeyError as exc:
        raise ValueError(f"关节轴必须是单位坐标轴，收到 {axis}") from exc

def create_ka_ur():
    mm = 0.001
    
    # ================= Link 1 (Base -> J1) =================
    # 这一段只有 Z 向平移，显示没问题
    l1 = rtb.ELink(
        rtb.ET.tz(169.3 * mm) * rtb.ET.Rz(),
        name="Joint1"
    )
    
    # ================= Link 2 (J1 -> J2) =================
    # 这一段只有 Y 向平移，显示没问题 (横着的圆柱)
    l2 = rtb.ELink(
        rtb.ET.ty(179.3 * mm) * rtb.ET.Rx(-90, 'deg') * rtb.ET.Rz(),
        name="Joint2",
        parent=l1
    )
    
    # ================= Link 3 (J2 -> J3) =================
    # 大臂：纯 Y 轴平移 (在当前坐标系下)，显示没问题
    l3 = rtb.ELink(
        rtb.ET.ty(-625 * mm) * rtb.ET.Rz(),
        name="Joint3",
        parent=l2
    )
    
    # ================= Link 4 (J3 -> J4) [优化显示] =================
    # 原逻辑: tz(-162.6) * ty(-595) -> 导致斜线
    # 新逻辑: 拆分为 l3_dummy (竖直) + l4 (水平)
    
    # 1. 虚连杆: 负责 Z 轴向下的偏移 (162.6mm)
    # 这是一个固定的几何体，没有关节变量
    l3_dummy = rtb.ELink(
        rtb.ET.tz(-162.6 * mm),
        name="Link3_Visual_Offset",
        parent=l3
    )
    
    # 2. 真实关节: 负责 Y 轴水平偏移 (595mm) 和旋转
    l4 = rtb.ELink(
        rtb.ET.ty(-595 * mm) * rtb.ET.Rz(),
        name="Joint4",
        parent=l3_dummy  # 连在虚连杆上
    )
    
    # ================= Link 5 (J4 -> J5) [优化显示] =================
    # 原逻辑: tz(105.2) ... -> 只有Z向，其实显示应该还好
    # 但为了更清晰，我们保持原样，因为它是纯 Z 轴平移后再变轴
    l5 = rtb.ELink(
        rtb.ET.tz(105.2 * mm) * rtb.ET.Rx(90, 'deg') * rtb.ET.Rz(),
        name="Joint5",
        parent=l4
    )
    
    # ================= Link 6 (J5 -> J6) [优化显示] =================
    # 原逻辑: ty(100) * tz(105.2) -> 同时有 Y 和 Z，会显示为斜线
    # 新逻辑: 拆分为 l5_dummy (水平) + l6 (垂直)

    # 1. 虚连杆: 负责 Y 轴水平偏移 (110.2mm)
    l5_dummy = rtb.ELink(
        rtb.ET.tz(105.2 * mm),
        name="Link5_Visual_Offset",
        parent=l5
    )
    
    # 2. 真实关节: 负责 Z 轴垂直偏移 (105.2mm) 和最终旋转
    l6 = rtb.ELink(
        rtb.ET.ty(110 * mm) * rtb.ET.Rx(-90, 'deg') * rtb.ET.Rz(),
        name="Joint6",
        parent=l5_dummy # 连在虚连杆上
    )
    
    # ================= 组装 =================
    # 注意：ERobot 会自动处理静态链接，不会增加关节自由度 (DOF)
    # 我们只需要把所有涉及到的 Link (包括 Dummy) 都放进去，或者只放叶子节点让它自动回溯
    # 最稳妥的方法是把所有定义的 Link 都放进去
    robot = rtb.ERobot(
        [l1, l2, l3, l3_dummy, l4, l5, l5_dummy, l6],
        name="kaanh_ur"
    )
    
    return robot

if __name__ == "__main__":
    robot = create_humaniod_robot()
    print(robot)
    robot.q = np.zeros(robot.n)

    try:
        print("\n打开 20 个关节的滑条窗口；拖动滑条检查各轴的正方向。")
        robot.teach(
            q=robot.q,
            backend="pyplot",
            limits=[-1.2, 1.2, -1.4, 1.4, 0.0, 2.1],
        )
    except Exception as e:
        print(f"滑条绘图错误: {e}")
