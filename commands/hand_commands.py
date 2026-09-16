class HandCommandExecutor:
    def __init__(self, robot):
        self.robot = robot

    def reinitialize(self, id=15):
        """灵巧手回0位，并重新使能"""
        self.robot.hand_home(id)
        self.robot.hand_en(id)

    def prepare(self, id=15):
        """灵巧手准备抓取"""
        self.robot.hand_move(            
            id=id,
            j1=6000,
            j2=0,
            j3=0,
            j4=0,
            j5=0,
            j6=0,
            vel=1000,
            cur=1000,)

    def grasp(self, id=15):
        """灵巧手抓取"""
        self.robot.hand_move(
            id=id,
            j1=6000,
            j2=5800,
            j3=7000,
            j4=7000,
            j5=7000,
            j6=4000,
            vel=1000,
            cur=1000,
        )

    def release(self, id=15):
        """灵巧手松开"""
        self.robot.hand_move(
            id=id,
            j1=0,
            j2=0,
            j3=0,
            j4=0,
            j5=0,
            j6=0,
            vel=1000,
            cur=1000,
        )
