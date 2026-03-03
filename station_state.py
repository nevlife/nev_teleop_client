class StationState:
    def __init__(self):
        self.linear_x:           float = 0.0
        self.steer_angle:        float = 0.0
        self.raw_speed:          float = 0.0
        self.raw_steer:          float = 0.0
        self.estop:              bool  = False
        self.joystick_connected: bool  = False
