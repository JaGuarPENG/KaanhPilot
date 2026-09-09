from robot.kaanh_backend import KaanhRobotBackend, TOTAL_JOINT_COUNT
from robot.robot_state import ModelState, RobotState, parse_robot_state


def test_build_movej_command_uses_the_verified_multi_model_layout():
    command = KaanhRobotBackend._build_movej_command(range(TOTAL_JOINT_COUNT))

    assert command == (
        "manual_mvaj --pos=JointTarget{"
        "OffsetSevenAxis_JointTarget{DOUBLE{0.000000},DOUBLE{1.000000},"
        "DOUBLE{2.000000},DOUBLE{3.000000},DOUBLE{4.000000},"
        "DOUBLE{5.000000},DOUBLE{6.000000}},"
        "OffsetSevenAxis_JointTarget{DOUBLE{7.000000},DOUBLE{8.000000},"
        "DOUBLE{9.000000},DOUBLE{10.000000},DOUBLE{11.000000},"
        "DOUBLE{12.000000},DOUBLE{13.000000}},"
        "AbenicsModel_JointTarget{DOUBLE{14.000000},DOUBLE{15.000000},"
        "DOUBLE{16.000000},DOUBLE{17.000000}},"
        "ExAxisModel_JointTarget{DOUBLE{18.000000}},"
        "ExAxisModel_JointTarget{DOUBLE{19.000000}}}"
    )


def test_movej_model_replaces_only_the_requested_model(monkeypatch):
    backend = KaanhRobotBackend("127.0.0.1")
    monkeypatch.setattr(
        backend,
        "get_robot_state",
        lambda: RobotState(actual_joints_deg=list(range(TOTAL_JOINT_COUNT))),
    )
    commands = []
    monkeypatch.setattr(
        backend,
        "_send_raw_command",
        lambda command: commands.append(command) or b"ok",
    )

    assert backend.movej_model(2, [100, 101, 102, 103]) == b"ok"

    command = commands[0]
    assert "AbenicsModel_JointTarget{DOUBLE{100.000000},DOUBLE{101.000000},DOUBLE{102.000000},DOUBLE{103.000000}}" in command
    assert "OffsetSevenAxis_JointTarget{DOUBLE{0.000000},DOUBLE{1.000000}" in command
    assert "ExAxisModel_JointTarget{DOUBLE{18.000000}}" in command


def test_movel_model_replaces_only_the_requested_arm(monkeypatch):
    backend = KaanhRobotBackend("127.0.0.1")
    state = RobotState(
        models=[
            ModelState(0, pe=[1, 2, 3, 4, 5, 6], axis_pe=[-84]),
            ModelState(1, pe=[7, 8, 9, 10, 11, 12], axis_pe=[84]),
            ModelState(2, pe=[13, 14, 15]),
            ModelState(3, pe=[16]),
            ModelState(4, pe=[17]),
        ]
    )
    monkeypatch.setattr(backend, "get_robot_state", lambda: state)
    commands = []
    monkeypatch.setattr(
        backend,
        "_send_raw_command",
        lambda command: commands.append(command) or b"ok",
    )

    assert backend.movel_model(1, [100, 101, 102, 103, 104, 105]) == b"ok"

    command = commands[0]
    assert command.startswith("mvl --pe=RobotTarget{")
    assert "EE_XYZABC{DOUBLE{100.000000},DOUBLE{101.000000},DOUBLE{102.000000},DOUBLE{103.000000},DOUBLE{104.000000},DOUBLE{105.000000},INT32{0},INT32{0},BOOL{false}}" in command
    assert "EE_ABC{DOUBLE{13.000000},DOUBLE{14.000000},DOUBLE{15.000000},INT32{0},INT32{0},BOOL{false}}" in command
    assert command.endswith("--vel=v50 --zone=z10 --pos_offset=o0 --ch=-1 --tg=2 --reinit=1")


def test_robot_state_keeps_non_six_dimensional_waist_and_head_pe():
    state = parse_robot_state(
        {
            "ret_context": {
                "motion_msg": {
                    "motor_pos": [[0] * 7, [0] * 7, [0] * 4, [0], [0]],
                    "pe": [
                        [1, 2, 3, 4, 5, 6], [-84],
                        [7, 8, 9, 10, 11, 12], [84],
                        [13, 14, 15], [16], [17],
                    ],
                }
            }
        }
    )

    assert state.models[0].pe == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    assert state.models[2].pe == [13.0, 14.0, 15.0]
    assert state.models[3].pe == [16.0]
    assert state.models[4].pe == [17.0]
