__author__ = "George Exarchakos"
__email__ = "g.exarchakos@tue.nl"
__version__ = "0.0.12"
__copyright__ = "Copyright 2015, The RICH Project"
#__credits__ = ["XYZ"]
#__maintainer__ = "XYZ"
#__license__ = "GPL"
#__status__ = "Production"

import sys

from core.schedule import SchedulerInterface, logg
from core.slotframe import Slotframe, Cell
from core.interface import BlockQueue
from example import main
from time import time
import adwin
from util import terms, logger
import logging
from txthings import coap

logg = logging.getLogger('RiSCHER')
logg.setLevel(logging.DEBUG)


class Plexiflex(SchedulerInterface):
	"""
	"""
	def __init__(self, net_name, lbr_ip, lbr_port, prefix, visualizer=None):
		super(Plexiflex,self).__init__(net_name, lbr_ip, lbr_port, prefix, visualizer)
		self.metainfo = {}
		self.pending_connects = []
		self.stats_ids = 1
		self.reserved_cells = []
		# Define a frame of size 11 slots containing unicast cells
		mainstream_frame = Slotframe("mainstream", 11)
		# Register that frame to the dictionary of frames of the parent Reflector
		self.frames[mainstream_frame.name] = mainstream_frame

	def start(self):
		self.pending_connects.append(self.root_id)
		self.metainfo[self.root_id] = {'adwin':adwin.Adwin(32), 'timestamp':-1,'pending_cells':[]}
		self.communicate(self.get_neighbor_of(self.root_id, False))
		super(Plexiflex, self).start()

	def connected(self, child, parent=None, old_parent=None):
		if child not in self.pending_connects:
			self.pending_connects.append(child)
			self.metainfo[child] = {'adwin':adwin.Adwin(32), 'timestamp':-1,'pending_cells':[]}
			self.communicate(self.get_neighbor_of(child, False))
		return None

	def disconnected(self, node_id):
		pass

	def rewired(self, node_id, old_parent, new_parent):
		pass

	def framed(self, who, local_name, remote_alias, old_payload):
		pass

	def celled(self, who, slotoffs, channeloffs, frame, linkoption, linktype, target, old_payload):
		for i in self.reserved_cells:
			if i.owner == who and i.slot == slotoffs and i.channel == channeloffs and i.slotframe == frame.get_alias_id(who):
				self.reserved_cells.remove(i)
				break
		if linkoption & 1 == 1:
			q = BlockQueue()
			cells = frame.get_cells_similar_to(
				owner=who,
				slot=slotoffs,
				channel=channeloffs,
				link_option=1
			)
			if len(cells) == 1:
				q.push(self.set_remote_statistics(who, self.stats_ids, cells[0], terms.resources['6TOP']['STATISTICS']['ETX']['LABEL'], 5))
				self.stats_ids += 1
			return q
		return None

	def deleted(self, who, resource, info):
		pass

	def reported(self, node, resource, value):
		q = BlockQueue()
		if node in self.pending_connects:
			if str(resource) == terms.get_resource_uri('6TOP', 'SLOTFRAME') and \
					self.frames['mainstream'].get_alias_id(node) != 255:
				add = True
				for f in self.frames.values():
					parts = f.name.split('#')
					if len(parts) == 2 and parts[0] == node.eui_64_ip and int(parts[1]) == 255:
						self.frames['mainstream'].set_alias_id(node, 255)
						del self.frames[f.name]
						add = False
						break
				if add:
					q.push(self.post_slotframes(node, self.frames['mainstream']))
			elif str(resource) == terms.get_resource_uri('6TOP', 'CELLLIST', 'ID') and value is not None:
				self.metainfo[node]['pending_cells'] += value
			elif str(resource).startswith(terms.get_resource_uri('6TOP', 'CELLLIST',ID='')) and value is not None:
				self.metainfo[node]['pending_cells'].remove(value[terms.resources['6TOP']['CELLLIST']['ID']['LABEL']])
				if not self.metainfo[node]['pending_cells']:
					self.pending_connects.remove(node)
					q.push(self._initiate_schedule(node))
		elif str(resource).startswith(terms.get_resource_uri('6TOP', 'NEIGHBORLIST')) and value is not None:
			last = self.metainfo[node]['timestamp']
			now = time()
			if last > 0:
				timelag = now - last
				trigger = self.metainfo[node]['adwin'].update(timelag)
				if trigger:
					print str(now)+": RESCHEDULE NOW!!!"
			self.metainfo[node]['timestamp'] = now
		elif str(resource).startswith(terms.get_resource_uri('6TOP', 'STATISTICS')) and value == coap.CHANGED:
			print "Hooray"
		return q


	def schedule(self, tx, rx, slotframe):
		"""
		Schedules a link at a given slotframe.

		Starts from slot 1 and channel 0. All the channels of the slot are scanned. If the intended link does not conflict
		with any simultaneous transmission at that slot and does not interfere with any other pair of nodes, the link is
		scheduled at that channel and slot. If no such channel can be found, the next slot is scanned.

		Note that the slots and channels of both Broadcast-Frame and Unicast-Frame slotframes are considered to avoid conflicts
		and interferences.

		:note: all 16 channels are assumed available

		:param tx: the transmitting node
		:type tx: NodeID
		:param rx: the receiving node or None if broadcast link to be scheduled
		:type rx: NodeID or None
		:param slotframe: the slotframe the link should be scheduled to
		:type slotframe: Slotframe
		:return: (slotoffset, channeloffset)
		:rtype: (int,int) tuple of (None,None) tuple if cell not found
		"""
		max_slots = 0
		for frame in self.frames.values():
			if max_slots < frame.slots:
				max_slots = frame.slots

		for slot in range(1, max_slots):
			skip = False
			free_channels = set(range(16))
			# Run through all available frames to detect channels that conflict or interfere with intended link
			for frame in self.frames.values():
				# Exclude those cells that interfere with tx->rx transmission
				free_channels = free_channels.difference(self.interfere(slot, tx, rx, frame, self.reserved_cells))
				# Take next slot, if there are no channels available or the tx->rx conflicts with another link at that slot

			for frame in self.frames.values():
				if len(free_channels) == 0 or self.conflict(slot, tx, rx, frame, self.reserved_cells):
					skip = True
					break

			# If all previous checks are passed, pick and return the slot and channel found
			if not skip:
				return slot, list(free_channels)[0]

			# If all slots of the target frame are checked without result, break and return (None,None)
			if slot == slotframe.slots-1:
				break

		return None, None

	def _initiate_schedule(self, node):
		cells = self.frames['mainstream'].get_cells_of(node)
		dag_neighbors = []
		parent = self.dodag.get_parent(node)
		if parent:
			dag_neighbors += [parent]
		children = self.dodag.get_children(node)
		if children:
			dag_neighbors += children
		q = BlockQueue()
		for neighbor in dag_neighbors:
			flags = 0
			for cell in cells:
				if cell.tna == neighbor:
					if cell.link_option & 1 == 1:
						flags |= 1
					elif cell.link_option & 2 == 2:
						flags |= 2
			if flags & 1 == 0:
				so,co = self.schedule(node, neighbor, self.frames["mainstream"])
				if so is not None and co is not None:
					q.push(self.post_link(so, co, self.frames["mainstream"], node, neighbor))
					self.reserved_cells.append(Cell(node,so,co,self.frames["mainstream"].get_alias_id(node),0,1,neighbor))
					self.reserved_cells.append(Cell(neighbor,so,co,self.frames["mainstream"].get_alias_id(neighbor),0,2,node))
				else:
					logg.critical("INSUFFICIENT SLOTS: node " + str(node) + " cannot use more cells")
			if flags & 2 == 0:
				so,co = self.schedule(neighbor, node, self.frames["mainstream"])
				if so is not None and co is not None:
					q.push(self.post_link(so, co, self.frames["mainstream"], neighbor, node))
					self.reserved_cells.append(Cell(node,so,co,self.frames["mainstream"].get_alias_id(node),0,2,neighbor))
					self.reserved_cells.append(Cell(neighbor,so,co,self.frames["mainstream"].get_alias_id(neighbor),0,1,node))
				else:
					logg.critical("INSUFFICIENT SLOTS: node " + str(neighbor) + " cannot use more cells")
		return q

if __name__ == '__main__':
	x = main.get_user_input(None)
	if isinstance(x, main.UserInput):
		sch = Plexiflex(x.network_name, x.lbr, x.port, x.prefix, False)
		sch.start()
		sys.exit(0)
	sys.exit(x)