#!/usr/bin/python3

# Note: This python script is used for temporary testing only.

import os
import time
import json
import threading
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from scipy.interpolate import interp1d
import numpy as np
from numpy import deg2rad, rad2deg



def save_subplot(save_dir, fig, ax, x_border, y_border):
    fig.savefig(save_dir, bbox_inches=ax.get_window_extent().transformed(fig.dpi_scale_trans.inverted()).expanded(x_border, y_border))

def get_cmd_spd(plot_item, scoop):
    # compute commanded spd
    cmd_spd = [0]
    cmd_t = [0]
    cmd_spd.append(0)
    t_wp = 130 # hardcode delay 
    cmd_t.append(t_wp)
    # reached const approaching spd
    cmd_spd.append(-scoop.smack_vel)
    t_wp = t_wp + round(scoop.smack_vel/scoop.smack_acc)*1000
    cmd_t.append(t_wp)
    # reached collision
    cmd_spd.append(-scoop.smack_vel)
    t_wp = plot_item['t_col'][0]
    cmd_t.append(t_wp)
    # reached const lifting spd
    acc_slow = (scoop.smack_vel**2) / (2*scoop.slow_dist)
    t_liftAcc = scoop.lift_vel / acc_slow
    s_liftAcc = 0.5 * acc_slow * t_liftAcc**2
    t_liftConstSpd = (scoop.lift_dist-s_liftAcc) / scoop.lift_vel
    cmd_spd.append(scoop.lift_vel)
    t_wp = t_wp + round(t_liftAcc*1000)
    cmd_t.append(t_wp)
    cmd_spd.append(scoop.lift_vel)
    t_wp = t_wp + round(t_liftConstSpd*1000)
    cmd_t.append(t_wp)
    # stop to zero spd
    cmd_spd.append(0)
    t_stop = scoop.lift_vel / scoop.stop_acc
    t_wp = t_wp + round(t_stop*1000)
    cmd_t.append(t_wp)
    # end point
    cmd_spd.append(0)
    cmd_t.append(plot_item['t1'][-1])
    return cmd_spd, cmd_t

def truncate_pos_data(x,y,displacement_threshold):
    '''
    Truncate redundent position data from the start and end if the distance of neighbour points are less than the threshold
    '''
    # truncate from the start
    n = len(x)
    trunc_idx = 0
    for i in range(n-1):
        a = np.array((x[i] ,y[i]))
        b = np.array((x[i+1], y[i+1]))
        dist_change = np.linalg.norm(a-b)
        if dist_change > displacement_threshold:
            trunc_idx = i
            break
    x_trunc = x[trunc_idx:]
    y_trunc = y[trunc_idx:]

    # truncate from the end
    n = len(x_trunc)
    trunc_idx = 0
    for i in range(n-1, 0, -1):
        a = np.array((x_trunc[i] ,y_trunc[i]))
        b = np.array((x_trunc[i-1], y_trunc[i-1]))
        dist_change = np.linalg.norm(a-b)
        if dist_change > displacement_threshold:
            trunc_idx = i
            break
    x_trunc = x_trunc[:trunc_idx]
    y_trunc = y_trunc[:trunc_idx]

    # return oringinal data if all the point below threshold
    if len(x_trunc) == 0:
        return x, y

    return x_trunc, y_trunc


def highResPoints(x,y,factor=10):
    '''
    Take points listed in two vectors and return them at a higher
    resultion. Create at least factor*len(x) new points that include the
    original points and those spaced in between.

    Returns new x and y arrays as a tuple (x,y).
    '''

    # r is the distance spanned between pairs of points
    r = [0]
    for i in range(1,len(x)):
        dx = x[i]-x[i-1]
        dy = y[i]-y[i-1]
        r.append(np.sqrt(dx*dx+dy*dy))
    r = np.array(r)

    # rtot is a cumulative sum of r, it's used to save time
    rtot = []
    for i in range(len(r)):
        rtot.append(r[0:i].sum())
    rtot.append(r.sum())

    dr = rtot[-1]/(len(x)*factor-1)
    xmod=[x[0]]
    ymod=[y[0]]
    rPos = 0 # current point on walk along data
    rcount = 1 
    while rPos < r.sum():
        x1,x2 = x[rcount-1],x[rcount]
        y1,y2 = y[rcount-1],y[rcount]
        dpos = rPos-rtot[rcount] 
        theta = np.arctan2((x2-x1),(y2-y1))
        rx = np.sin(theta)*dpos+x1
        ry = np.cos(theta)*dpos+y1
        xmod.append(rx)
        ymod.append(ry)
        rPos+=dr
        while rPos > rtot[rcount+1]:
            rPos = rtot[rcount+1]
            rcount+=1
            if rcount >= len(rtot)-1: #rcount>rtot[-1]:
                break

    return xmod,ymod


class DataLogger:

    def __init__(self, robot, gripper, scooping_primitives):
        self.ur = robot
        self.ddh = gripper
        self.scoop = scooping_primitives

        self.keep_logging = False
        self.log_rates = [500, 100, 200] # no. of element determines no. of threads
        self.logging_threads = [None for i in self.log_rates]
        self.logged_data_sets = [[] for i in self.log_rates]
        self.collision_time = None

    def logging_job1(self):
        while self.keep_logging:
            time.sleep(1.0/self.log_rates[0])
            data = {
                't1': round(time.time() * 1000),
                'L0': self.ddh.link_pos_l0,
                'L1': self.ddh.link_pos_l1,
                'R0': self.ddh.link_pos_r0,
                'R1': self.ddh.link_pos_r1,
                'L0_cmd': self.ddh.cmd_pos_l0,
                'L1_cmd': self.ddh.cmd_pos_l1,
                'R0_cmd': self.ddh.cmd_pos_r0,
                'R1_cmd': self.ddh.cmd_pos_r1,
                'L0_cur': self.ddh.finger_L.axis0.motor.current_meas_phC, #B / C
                'L1_cur': self.ddh.finger_L.axis1.motor.current_meas_phC,
                'R0_cur': self.ddh.finger_R.axis0.motor.current_meas_phC,
                'R1_cur': self.ddh.finger_R.axis1.motor.current_meas_phC
                # 'L0_cur': self.ddh.finger_L.axis0.motor.DC_calib_phB, # unknow voltage / current 
                # 'L1_cur': self.ddh.finger_L.axis1.motor.DC_calib_phB,
                # 'R0_cur': self.ddh.finger_R.axis0.motor.DC_calib_phB,
                # 'R1_cur': self.ddh.finger_R.axis1.motor.DC_calib_phB
            }
            self.logged_data_sets[0].append(data)
        
    def logging_job2(self):
        while self.keep_logging:
            time.sleep(1.0/self.log_rates[1])
            data = {
                't2': round(time.time() * 1000),
                'UR_Z': self.ur.getl(wait=True,_log=False)[2]
            }
            self.logged_data_sets[1].append(data)

    def logging_job3(self):
        while self.keep_logging:
            time.sleep(1.0/self.log_rates[2])
            data = {
                't3': round(time.time() * 1000),
                'L_stiff': self.ddh.cmd_stiff_l,
                'R_stiff': self.ddh.cmd_stiff_r,
                'UR_Z_SPD': self.ur.get_tcp_speed(wait=False)[2],
            }
            self.logged_data_sets[2].append(data)
            if self.scoop.collided:
                self.collision_time = data['t3']
                self.scoop.collided = False

    @property
    def logged(self):
        return all(x is not None for x in self.logging_threads)

    @logged.setter
    def logged(self, log):
        if log and not self.logged:
            self.log_start()
        if not log and self.logged:
            self.log_stop()
    
    def log_start(self):
        for s in self.logged_data_sets:
            s.clear()
        self.keep_logging = True
        jobs = [self.logging_job1, self.logging_job2, self.logging_job3]
        for idx in range(len(self.log_rates)):
            self.logging_threads[idx] = threading.Thread(target=jobs[idx])
            self.logging_threads[idx].start()
        print('Start logging')

    def log_stop(self):
        self.keep_logging = False
        for t in self.logging_threads:
            t.join()
        self.logging_threads = [None for i in self.log_rates]
        print('Stop logging')

    def plot_data(self, save_plot = False):
        plot_item = {}
        for s in self.logged_data_sets:
            for name in s[0]:
                plot_item[name] = [] # create empty list for each item

        # convert logged_data_set(list of dict) to plot_item(dict of list)
        for s in self.logged_data_sets:
            for data in s:
                for name in data:
                    if name == 't1' or name == 't2' or name == 't3': 
                        plot_item[name].append(data[name]-s[0][name]) # relative time from start logging
                    else: plot_item[name].append(data[name])
        
        if self.collision_time is not None:
            self.collision_time = self.collision_time - self.logged_data_sets[2][0]['t3']
            plot_item['t_col'] = [self.collision_time]
            print("Collision time: {} ms".format(self.collision_time))
        
        # creat subsplots
        fig1, ax1 = plt.subplots(1,2)
        fig2, ax2 = plt.subplots(1,2)
        fig3, ax3 = plt.subplots(1,2)
        # x axis margin
        max_t = max([plot_item['t1'][-1], plot_item['t2'][-1], plot_item['t3'][-1]])
        x_boundary = 0.05
        x_min = max_t * -x_boundary
        x_max = max_t * (1 + x_boundary)

        # plot motor angle
        ax1[0].plot(plot_item['t1'], plot_item['L0'], label='F0', color='tab:blue')
        ax1[0].plot(plot_item['t1'], plot_item['L1'], label='F1', color='tab:orange')
        ax1[0].plot(plot_item['t1'], plot_item['R0'], label='T0', color='tab:green')
        ax1[0].plot(plot_item['t1'], plot_item['R1'], label='T1', color='tab:red')
        ax1[0].plot(plot_item['t1'], plot_item['L0_cmd'], color='tab:blue', linestyle='--')
        ax1[0].plot(plot_item['t1'], plot_item['L1_cmd'], color='tab:orange', linestyle='--')
        ax1[0].plot(plot_item['t1'], plot_item['R0_cmd'], color='tab:green', linestyle='--')
        ax1[0].plot(plot_item['t1'], plot_item['R1_cmd'], color='tab:red', linestyle='--')
        if self.collision_time is not None: ax1[0].axvline(x=plot_item['t_col'], color='darkgray', linewidth='1.5', linestyle='--')
        ax1[0].legend(loc='upper right').get_frame().set_linewidth(1.0)
        ax1[0].set_title("Motor joint angles")
        ax1[0].set_ylabel("Angle (degree)")
        ax1[0].set_xlabel("Time (ms)")
        ax1[0].set_xlim((x_min,x_max))
        ax1[0].grid(True)

        # plot motor current
        ax1[1].plot(plot_item['t1'], plot_item['L0_cur'], label='F0', color='tab:blue')
        ax1[1].plot(plot_item['t1'], plot_item['L1_cur'], label='F1', color='tab:orange')
        ax1[1].plot(plot_item['t1'], plot_item['R0_cur'], label='T0', color='tab:green')
        ax1[1].plot(plot_item['t1'], plot_item['R1_cur'], label='T1', color='tab:red')
        if self.collision_time is not None: ax1[1].axvline(x=plot_item['t_col'], color='darkgray', linewidth='1.5' ,linestyle='--')
        ax1[1].legend(loc='upper right').get_frame().set_linewidth(1.0)
        ax1[1].set_title("Motor current")
        ax1[1].set_ylabel("Current (A)")
        ax1[1].set_xlabel("Time (ms)")
        ax1[1].set_xlim((x_min,x_max))
        ax1[1].grid(True)

        # #compute psi angle from link angle
        # psi = []
        # for i in range(len(plot_item['t1'])):
        #     psi.append(self.ddh.link_to_phi(plot_item['R0'][i],plot_item['R1'][i],'R')+self.scoop.theta)
        # plot_item['psi'] = psi

        # # plot angle of attack
        # ax1[1].plot(plot_item['t1'], plot_item['psi'])
        # if self.collision_time is not None: ax1[1].axvline(x=plot_item['t_col'], color='darkgray', linewidth='1.5' ,linestyle='--')
        # ax1[1].set_title("Psi (angle of attack)")
        # ax1[1].set_ylabel("Angle (degree)")
        # ax1[1].set_xlabel("Time (ms)")
        # ax1[1].set_xlim((x_min,x_max))
        # ax1[1].grid(True)

        # plot stiffness
        ax2[0].plot(plot_item['t3'], plot_item['L_stiff'], label='F_Kp')
        ax2[0].plot(plot_item['t3'], plot_item['R_stiff'], label='T_Kp')
        if self.collision_time is not None: ax2[0].axvline(x=plot_item['t_col'], color='darkgray', linewidth='1.5' ,linestyle='--')
        ax2[0].legend(loc='lower right').get_frame().set_linewidth(1.0)
        ax2[0].set_title("Digit stiffness")
        ax2[0].set_ylabel("P-Gain ((turn/s) / turn)")
        ax2[0].set_xlabel("Time (ms)")
        ax2[0].set_xlim((x_min,x_max))
        ax2[0].grid(True)

        # plot fingertips trajectory
        L_tip_x = []
        L_tip_y = []
        R_tip_x = []
        R_tip_y = []
        # get current gripper tilt angle 
        theta = 90 - rad2deg(self.ur.get_orientation().to_euler('XZX')[2])
        print('Gripper tilt angle: {:.2f} degree'.format(theta))
        for i in range(len(plot_item['t1'])):
            # get tip position in motor frame
            lx, ly = self.ddh.link_to_tip(plot_item['L0'][i],plot_item['L1'][i],'L')
            rx, ry = self.ddh.link_to_tip(plot_item['R0'][i],plot_item['R1'][i],'R')
            # shift from motor frame to gripper frame
            ly = ly + self.scoop.P_g_L[1]
            ry = ry + self.scoop.P_g_R[1]
            # transform the orientation from motor frame to world frame
            q = theta - 180 # angle to rotate 
            lx_world = lx * np.cos(deg2rad(q)) - ly * np.sin(deg2rad(q))
            ly_world = ly * np.cos(deg2rad(q)) + lx * np.sin(deg2rad(q))
            rx_world = rx * np.cos(deg2rad(q)) - ry * np.sin(deg2rad(q))
            ry_world = ry * np.cos(deg2rad(q)) + rx * np.sin(deg2rad(q))
            L_tip_x.append(lx_world)
            L_tip_y.append(ly_world)
            R_tip_x.append(rx_world)
            R_tip_y.append(ry_world)

        L_tip_x,L_tip_y = truncate_pos_data(L_tip_x,L_tip_y, 0.3)
        R_tip_x,R_tip_y = truncate_pos_data(R_tip_x,R_tip_y, 0.3)

        lx,ly = highResPoints(L_tip_x,L_tip_y,5)
        ln = len(lx)
        for i in range(ln-1):
            ax2[1].plot(lx[i:i+2],ly[i:i+2], color='tab:blue', alpha = float(i)/(ln-1)) 
        
        rx,ry = highResPoints(R_tip_x,R_tip_y,5)
        rn = len(rx)
        for j in range(rn-1):
            ax2[1].plot(rx[j:j+2],ry[j:j+2], color='tab:red', alpha = float(j)/(rn-1)) 
        colors = ['tab:blue', 'tab:red']
        lines = [Line2D([0], [0], color=c) for c in colors]
        labels = ['Fingertip', 'Thumbtip']
        ax2[1].legend(lines, labels, loc='upper right').get_frame().set_linewidth(1.0)
        ax2[1].set_xticks(range(-300,300,20))
        ax2[1].set_yticks(range(-300,300,20))
        ax2[1].axis('equal')
        ax2[1].set_title("Digit tips position")
        ax2[1].set_ylabel("z-position (mm)")
        ax2[1].set_xlabel("x-position (mm)")

        # plot ur z
        ur_z_interp = interp1d(plot_item['t2'],plot_item['UR_Z'],kind="cubic")
        t_sample = np.linspace(plot_item['t2'][0],plot_item['t2'][-1],100)
        ur_z_new = ur_z_interp(t_sample)
        ax3[0].plot(t_sample, ur_z_new)
        # ax3[0].plot(plot_item['t2'], plot_item['UR_Z'])
        if self.collision_time is not None: ax3[0].axvline(x=plot_item['t_col'], color='darkgray', linewidth='1.5' ,linestyle='--')
        ax3[0].set_title("Height of arm")
        ax3[0].set_ylabel("z-position (m)")
        ax3[0].set_xlabel("Time (ms)")
        ax3[0].set_xlim((x_min,x_max))
        ax3[0].grid(True)

        # plot actual ur z spd
        if self.collision_time is not None:
            plot_item['cmd_spd'], plot_item['cmd_t'] = get_cmd_spd(plot_item, self.scoop)
            ax3[1].plot(plot_item['cmd_t'],plot_item['cmd_spd'], linestyle='--', color='tab:red')
            ax3[1].axvline(x=plot_item['t_col'], color='darkgray', linewidth='1.5' ,linestyle='--')
        ax3[1].plot(plot_item['t3'], plot_item['UR_Z_SPD'], color='tab:red')
        ax3[1].set_title("Speed of arm")
        ax3[1].set_ylabel("z-speed (m/s)")
        ax3[1].set_xlabel("Time (ms)")
        ax3[1].set_xlim((x_min,x_max))
        ax3[1].grid(True)

        if save_plot: 
            currentTime = time.strftime("%Y-%m-%d_%H:%M:%S", time.localtime())
            save_dir = 'plots/' + str(currentTime)
            os.makedirs(save_dir)

            # save subplots as figs
            save_subplot(save_dir + '/joint.png', fig1, ax1[0], 1.23, 1.23)
            save_subplot(save_dir + '/psi.png', fig1, ax1[1], 1.23, 1.23)
            save_subplot(save_dir + '/gain.png', fig2, ax2[0], 1.23, 1.23)
            save_subplot(save_dir + '/z_pos.png', fig3, ax3[0], 1.23, 1.23)
            save_subplot(save_dir + '/z_spd.png', fig3, ax3[1], 1.23, 1.23)

            # save logged data
            with open(save_dir + '/data.json', mode='w') as f:
                json.dump(plot_item, f)

        plt.show()
        self.collision_time = None