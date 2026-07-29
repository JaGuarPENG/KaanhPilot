import roboticstoolbox as rtb
import numpy as np

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
    robot = create_ka_ur()
    print(robot) # 你会发现它还是 6 轴 (n=6)，虚连杆不会增加自由度
    
    q0 = np.zeros(6)
    
    try:
        env = rtb.backends.PyPlot.PyPlot()
        env.launch()
        env.add(robot)
        
        robot.q = q0

        T0 = robot.fkine(q0)
        print("\n【零位末端位姿】")
        print(T0)
        
        # 调整视角以看清“直角”结构
        env.ax.set_xlim([-0.8, 0.8])
        env.ax.set_ylim([-0.8, 0.8])
        env.ax.set_zlim([0.0, 1.2])
        
        print("\n正在显示优化后的模型...")
        env.hold()
    except Exception as e:
        print(f"绘图错误: {e}")