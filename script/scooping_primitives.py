#!/usr/bin/python3
import time
from math import sin, cos, radians, pi
import numpy as np
from numpy import deg2rad, rad2deg
import yaml
import math3d as m3d



class HighSpeedScooping:

    def __init__(self, robot, gripper, config_name):
        self.ur = robot
        self.ddh = gripper
        config_file = config_name + ".yaml"
        with open("../config/"+config_file, 'r') as stream:
            try:
                print('reading scooping config...')
                config = yaml.safe_load(stream)
            except yaml.YAMLError as exc:
                print(exc)

        self.is_init = False
        self.collided = False

        self.obj_l = config['object_length']
        self.obj_t = config['object_thickness']
        # parameters for pre-scooping
        self.theta = config['gripper_tilt']
        self.grip_h = config['gripper_height']
        self.cont_dist = config['contact_distance']
        self.fg_dist = config['finger_prescoop_position'] * self.obj_l
        self.tb_dist = config['thumb_prescoop_position'] * self.obj_l
        self.center_dist = config['gripper_center'] * self.obj_l
        self.fg_pre_stiff = config['finger_prescoop_stiffness']
        self.tb_pre_stiff = config['thumb_prescoop_stiffness']
        self.T_t_g = np.array(config['T_tool_gripper'])
        self.P_g_L = np.array(config['P_gripper_MotorL'])
        self.P_g_R = np.array(config['P_gripper_MotorR'])
        self.init_vel = config['init_vel']
        self.init_acc = config['init_acc']
        self.digits_spd = config['digits_speed'] 
        # parameters for scooping
        self.smack_vel = config['smack_vel']
        self.smack_acc = config['smack_acc']
        self.slow_dist = config['slow_dist']
        self.lift_vel = config['lift_vel']
        self.lift_dist = config['lift_dist']
        self.stop_acc = config['stop_acc']
        self.fg_scp_stiff = config['finger_scoop_stiffness']
        self.tb_scp_stiff = config['thumb_scoop_stiffness']
        self.fg_gsp_stiff = config['finger_grasp_stiffness']
        self.tb_gsp_stiff = config['thumb_grasp_stiffness']
        self.grasp_pos = np.array(config['digit_grasp_position'])

        self.table2bot = 0.02 - 0.005 # robot base offset from table (m)
        self.wall2bot = 1.07 # distance from robot base to wall (m)


    def initialize_pose(self, object_2D_pose):
        ''' Initialize robot pose before scooping with the gripper setting above the object
        Parameters:
            object_2D_pose(tuple): (x_obj, y_obj, q_obj) object pose in x-y plane of ur10 base frame 
                x_obj(m): x coordinate of object's center
                y_obj(m): y coordinate of object's center
                q_obj(degree): angle of thumb's penetration axis relative to x axis of ur10 base frame
        Returns:
        
        '''
        x_obj, y_obj, q_obj = object_2D_pose
        
        # ======set fingers position according to scooping model======
        # compute F, T position in gripper frame
        y_g_F = -(self.fg_dist - self.center_dist) * sin(radians(self.theta))
        x_g_F = self.cont_dist + (self.fg_dist - self.center_dist) * cos(radians(self.theta))
        P_g_F = np.array([x_g_F, y_g_F, 0])
        y_g_T = (self.center_dist + self.tb_dist) * sin(radians(self.theta))
        x_g_T = self.cont_dist - (self.center_dist + self.tb_dist) * cos(radians(self.theta))
        P_g_T = np.array([x_g_T, y_g_T, 0])
        # F, T target position in their motor frame
        P_L_F = P_g_F - self.P_g_L
        P_R_T = P_g_T - self.P_g_R
        self.ddh.arm(pos_gain=self.fg_pre_stiff, BW=self.digits_spd, finger='L')
        self.ddh.arm(pos_gain=self.tb_pre_stiff, BW=self.digits_spd, finger='R')
        # self.ddh.set_stiffness(self.fg_pre_stiff, 'L')
        # self.ddh.set_stiffness(self.tb_pre_stiff, 'R')
        self.ddh.set_left_tip(tuple(P_L_F[:-1]))
        self.ddh.set_right_tip(tuple(P_R_T[:-1]))

        # ======set gripper pose according to object pose and tilt angle======
        # F position in tool frame
        P_t_F = np.matmul(self.T_t_g, np.append(P_g_F, 1))[:-1]
        # set F as tcp
        P_t_F = P_t_F / 1000
        self.ur.set_tcp(np.append(P_t_F, [0,0,0]))
        # set gripper initial pose
        init_pose = m3d.Transform()
        init_pose.pos.x = x_obj + (self.fg_dist - self.obj_l/2) * cos(radians(q_obj)) / 1000
        init_pose.pos.y = y_obj + (self.fg_dist - self.obj_l/2) * sin(radians(q_obj)) / 1000
        init_pose.pos.z = self.grip_h
        init_pose.orient.rotate_xb(pi)
        init_pose.orient.rotate_zt(radians(90-q_obj))
        init_pose.orient.rotate_xt(radians(90-self.theta))
        print("Setting pose: ")
        print(init_pose.pose_vector)
        self.ur.set_pose(init_pose, self.init_vel, self.init_acc)
        self.is_init = True

    def simple_scoop(self):
        ''' Close the fingers directly after the collision is detected
        Parameters:
        Returns:
        '''
        spd_collide = 0.0 # speed at collision
        pos_collide = 0.0 # position at collision
        acc_slow = 0.0 # acceleration to slow down
        pos_stop = 0.0 # position at zero speed
        t_liftAcc = 0.0 # time of acceleration during lift
        s_liftAcc = 0.0 # dist of accelertion during lift
        t_liftConstSpd = 0.0 # time of constant speed during lift
        pos_end = 0.0 # position after all movement ends

        if not self.is_init:
            print("Not initialized! Please run initialize_pose() first.")
            return
        else:
            self.is_init = False

        try: 
            # initial right finger a2 angle
            a2_init = self.ddh.right_a2 
            # accelerate gripper to the surface
            self.ur.speedl([0,0,-self.smack_vel,0,0,0],self.smack_acc,5)
            # collision detection loop
            while 1: 
                # get current right finger a2 angle
                a2_cur = self.ddh.right_a2 
                if a2_cur - a2_init > 0.3:
                    self.collided = True
                    spd_collide = self.ur.get_tcp_speed(wait=False)[2]
                    if spd_collide == 0.0:
                        print("Warning: get_tcp_speed() return 0.0!")
                        spd_collide = -self.smack_vel
                    pos_collide = self.ur.getl()[2]
                    print ("Collision detected!")
                    # close fingers with different stiffness
                    # self.ddh.arm(pos_gain=self.fg_scp_stiff, BW=self.digits_spd, finger='L') #for fragile
                    self.ddh.set_stiffness(self.fg_scp_stiff, 'L')
                    self.ddh.set_stiffness(self.tb_scp_stiff, 'R')
                    self.ddh.set_left_tip(tuple(self.grasp_pos))
                    self.ddh.set_right_tip(tuple(self.grasp_pos * [1,-1]))
                    # slow down gripper according to given decelerating distance
                    acc_slow = (spd_collide**2) / (2*self.slow_dist)
                    self.ur.speedl([0,0,self.lift_vel,0,0,0],acc_slow,5)
                    # wait until robot reach zero speed
                    while self.ur.get_tcp_speed(wait=False)[2] < 0:
                        continue
                    pos_stop = self.ur.getl()[2]
                    print("Reached zero speed!")
                    # increase fingers stiffness for grasping
                    self.ddh.set_stiffness(self.fg_gsp_stiff, 'L')
                    self.ddh.set_stiffness(self.tb_gsp_stiff, 'R')
                    # compute sleep time according to the distance to lift
                    t_liftAcc = self.lift_vel / acc_slow
                    s_liftAcc = 0.5 * acc_slow * t_liftAcc**2
                    t_liftConstSpd = (self.lift_dist-s_liftAcc) / self.lift_vel
                    # sleep until reached to lifted distance
                    time.sleep(t_liftAcc + t_liftConstSpd)
                    # terminate robot motion
                    self.ur.stopl(self.stop_acc)
                    break
            time.sleep(1)
            pos_end = self.ur.getl()[2]
            print("==========Scooping completed!==========")
            print("Speed at collision: {:.5f} m/s".format(spd_collide))
            print("Distance to decelerate: {:.5f} m".format(pos_collide - pos_stop))
            print("Deceleration for lifting: {:.5f} m/s^2".format(acc_slow))
            print("Slept time for lifting: {:.2f} s".format(t_liftAcc + t_liftConstSpd))
            print("Lifted distance: {:.5f} m".format(pos_end - pos_stop))
            self.collided = False
        except Exception as err:
            self.ur.stopl(5)
            print("Error occurred:")
            print(err)

    def wait_tip_idle(self, dist_threshold=0.5):
        prev_L_tip = np.array([0,0])
        prev_R_tip = np.array([0,0])
        cur_L_tip = np.array([100,100])
        cur_R_tip = np.array([100,100])
        while np.linalg.norm(cur_L_tip - prev_L_tip) > dist_threshold or np.linalg.norm(cur_R_tip - prev_R_tip) > dist_threshold:
            time.sleep(0.2)
            prev_L_tip = cur_L_tip
            prev_R_tip = cur_R_tip
            cur_L_tip = np.array(self.ddh.left_tip_pos)
            cur_R_tip = np.array(self.ddh.right_tip_pos)

    def prescooping_search(self, object_2D_pose, logger):
        x_obj, y_obj, q_obj = object_2D_pose

        # set arm pose
        tcp = np.matmul(self.T_t_g, np.append([0,0,0], 1))[:-1]
        self.ur.set_tcp(np.append(tcp/1000, [0,0,0])) # set gripper frame as tcp
        init_pose = m3d.Transform()
        # work in -ve x and +ve y region only
        init_pose.pos.x = x_obj + (self.fg_dist - self.obj_l/2) * cos(radians(q_obj)) / 1000
        init_pose.pos.y = y_obj + (self.fg_dist - self.obj_l/2) * sin(radians(q_obj)) / 1000
        init_pose.pos.z = self.grip_h
        init_pose.orient.rotate_xb(pi)
        init_pose.orient.rotate_zt(radians(90-q_obj))
        print("Setting pose: ")
        print(init_pose.pose_vector)
        self.ur.set_pose(init_pose, self.init_vel, self.init_acc)

        self.ddh.arm(pos_gain = 5, BW = 100)
        r = 150
        angle_start = 45 # relative to motor x axis
        angle_end = -45
        left_angle = 0
        lx = r * np.cos(deg2rad(left_angle))
        ly = r * np.sin(deg2rad(left_angle))
        self.ddh.set_left_tip((lx,ly))
        for q in range(angle_start, angle_end, -1):
            x = r * np.cos(deg2rad(q))
            y = r * np.sin(deg2rad(q))
            self.ddh.set_right_tip((x,y))
            # self.ddh.set_left_tip((x,-y))
            if q == angle_start:
                self.wait_tip_idle()
                self.ddh.set_bandwidth(5)
                logger.logged = True
        
        self.wait_tip_idle()
        logger.logged = False
            

    def wall_init(self, obj_x_pos, thumb_offset, side_scoop = False):
        ''' Pick up object leaning on the wall, robot approaching horizontally, Finger as R, thumb as L
        Parameters:
        Returns:
        '''

        # ======set fingers position according to scooping model======
        # compute F, T position in gripper frame
        y_g_F = -(self.fg_dist - self.center_dist) * sin(radians(self.theta))
        x_g_F = self.cont_dist + (self.fg_dist - self.center_dist) * cos(radians(self.theta))
        P_g_F = np.array([x_g_F, -y_g_F, 0]) # mirror at x axis
        y_g_T = (self.center_dist + self.tb_dist) * sin(radians(self.theta))
        x_g_T = self.cont_dist - (self.center_dist + self.tb_dist) * cos(radians(self.theta))
        P_g_T = np.array([x_g_T, -y_g_T, 0]) # mirror at x axis
        P_g_T = P_g_T + [thumb_offset*sin(radians(self.theta)), -thumb_offset*cos(radians(self.theta)), 0] # thumb offset: increase contact range
        # F, T target position in their motor frame
        P_R_F = P_g_F - self.P_g_R
        P_L_T = P_g_T - self.P_g_L
        self.ddh.arm(pos_gain=self.fg_pre_stiff, BW=self.digits_spd, finger='R')
        self.ddh.arm(pos_gain=self.tb_pre_stiff, BW=self.digits_spd, finger='L')
        self.ddh.set_left_tip(tuple(P_L_T[:-1]))
        self.ddh.set_right_tip(tuple(P_R_F[:-1]))

        # ======set gripper pose according to object pose and tilt angle======
        # F position in tool frame
        P_t_F = np.matmul(self.T_t_g, np.append(P_g_F, 1))[:-1]
        # set F as tcp
        P_t_F = P_t_F / 1000
        self.ur.set_tcp(np.append(P_t_F, [0,0,0]))
        # set gripper initial pose
        init_pose = m3d.Transform()
        # check below before execute !!!
        if side_scoop:
            init_pose.pos.x = obj_x_pos - (self.fg_dist - self.obj_l/2) / 1000
            obj_width = 0.2 # 20cm
            init_pose.pos.z = obj_width/2 - self.table2bot + 0.11 #box height for level up the surface
        else:
            init_pose.pos.x = obj_x_pos
            init_pose.pos.z = (self.obj_l/2)/1000 - self.table2bot - (self.fg_dist - self.obj_l/2) / 1000
        init_pose.pos.y = self.wall2bot - self.grip_h
        init_pose.orient.rotate_xb(pi)
        init_pose.orient.rotate_xt(radians(self.theta))
        if side_scoop:
            init_pose.orient.rotate_yb(pi/2)
        print("Setting pose: ")
        print(init_pose.pose_vector)
        self.ur.set_pose(init_pose, self.init_vel, self.init_acc)
        # if side_scoop:
        #     side_pose = self.ur.get_pose()
        #     side_pose.orient.rotate_yb(pi/2)
        #     self.ur.set_pose(side_pose, self.init_vel, self.init_acc)
        self.is_init = True

    def wall_scoop(self, side_scoop = False):
        ''' wall exp: Close the fingers directly after the collision is detected
        Parameters:
        Returns:
        '''
        spd_collide = 0.0 # speed at collision
        pos_collide = 0.0 # position at collision
        acc_slow = 0.0 # acceleration to slow down
        pos_stop = 0.0 # position at zero speed
        t_liftAcc = 0.0 # time of acceleration during lift
        s_liftAcc = 0.0 # dist of accelertion during lift
        t_liftConstSpd = 0.0 # time of constant speed during lift
        pos_end = 0.0 # position after all movement ends

        if not self.is_init:
            print("Not initialized! Please run initialize_pose() first.")
            return
        else:
            self.is_init = False

        try: 
            # initial left finger a2 angle
            a2_init = self.ddh.left_a2 
            # accelerate gripper to the surface
            self.ur.speedl([0,self.smack_vel,0,0,0,0],self.smack_acc,5)
            # collision detection loop
            while 1: 
                # get current right finger a2 angle
                a2_cur = self.ddh.left_a2 
                if a2_cur - a2_init > 0.3:
                    self.collided = True
                    spd_collide = self.ur.get_tcp_speed(wait=False)[1]
                    pos_collide = self.ur.getl()[1]
                    print ("Collision detected!")
                    # close fingers with different stiffness
                    self.ddh.set_stiffness(self.fg_scp_stiff, 'R')
                    self.ddh.set_stiffness(self.tb_scp_stiff, 'L')
                    self.ddh.set_left_tip(tuple(self.grasp_pos))
                    self.ddh.set_right_tip(tuple(self.grasp_pos * [1,-1]))
                    # slow down gripper according to given decelerating distance
                    acc_slow = (spd_collide**2) / (2*self.slow_dist)
                    self.ur.stopl(acc_slow)
                    # self.ur.speedl([0,-self.lift_vel,0,0,0,0],acc_slow,5)
                    # wait until robot reach zero speed
                    while self.ur.get_tcp_speed(wait=False)[1] > 0:
                        continue
                    print("Reached zero speed!")
                    # increase fingers stiffness for grasping
                    if side_scoop: 
                        end_pose = self.ur.get_pose()
                        end_pose.pos.y = self.wall2bot-self.grip_h
                        end_pose.pos.z = 0.3
                        end_pose.orient.rotate_xb(-pi/2)
                        self.ur.set_pose(end_pose, self.lift_vel, self.lift_vel)
                    else:
                        self.ur.set_pos((self.ur.getl()[0],self.wall2bot-self.grip_h,0.3), acc = self.lift_vel, vel = self.lift_vel, wait=False)
                    self.ddh.set_stiffness(self.fg_gsp_stiff, 'R')
                    self.ddh.set_stiffness(self.tb_gsp_stiff, 'L')
                    # # compute sleep time according to the distance to lift
                    # pos_stop = self.ur.getl()[1]
                    # t_liftAcc = self.lift_vel / acc_slow
                    # s_liftAcc = 0.5 * acc_slow * t_liftAcc**2
                    # t_liftConstSpd = (self.lift_dist-s_liftAcc) / self.lift_vel
                    # # sleep until reached to lifted distance
                    # time.sleep(t_liftAcc + t_liftConstSpd)
                    # # terminate robot motion
                    # self.ur.stopl(self.stop_acc)
                    break
            time.sleep(1)
            pos_end = self.ur.getl()[1]
            print("==========Scooping completed!==========")
            print("Speed at collision: {:.5f} m/s".format(spd_collide))
            print("Distance to decelerate: {:.5f} m".format(pos_collide - pos_stop))
            print("Deceleration for lifting: {:.5f} m/s^2".format(acc_slow))
            print("Slept time for lifting: {:.2f} s".format(t_liftAcc + t_liftConstSpd))
            print("Lifted distance: {:.5f} m".format(pos_end - pos_stop))
            self.collided = False
        except Exception as err:
            self.ur.stopl(5)
            print("Error occurred:")
            print(err)

