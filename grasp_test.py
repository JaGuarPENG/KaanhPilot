from commands.snapshot_pick_command import SnapshotPickCommand
from commands.robot_commands import RobotCommandExecutor
from robot.agv_backend import AGVBackend
from robot.kaanh_backend import KaanhRobotBackend

def grasp_test():
    robot = KaanhRobotBackend("192.168.100.99", 5888, 9998, 10)
    robot.connect()
    robot.login("Engineer", "000000")
    robot.manual_enable()
    robot.set_jog_coordinate()
    robot.set_pgm_vel(50)
    robot.set_jog_vel(50)
    robot_executor = RobotCommandExecutor(robot)
    grasp_executor = SnapshotPickCommand(robot)

    robot_executor.move_init_pose()
    grasp_executor.initialize_resources()
    grasp_executor.pick("oolong_tea")
    robot_executor.move_init_pose()

    



if __name__ == "__main__":
    grasp_test()