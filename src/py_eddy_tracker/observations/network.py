# -*- coding: utf-8 -*-
"""
Class to create network of observations
"""
import logging
from glob import glob

from numba import njit
from numpy import arange, array, bincount, empty, ones, uint32, unique

from ..poly import bbox_intersection, vertice_overlap
from .observation import EddiesObservations
from .tracking import TrackEddiesObservations

logger = logging.getLogger("pet")


class Singleton(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super().__call__(*args, **kwargs)
        return cls._instances[cls]


class Buffer(metaclass=Singleton):
    __slots__ = (
        "buffersize",
        "contour_name",
        "xname",
        "yname",
        "memory",
    )
    DATA = dict()
    FLIST = list()

    def __init__(self, buffersize, intern=False, memory=False):
        self.buffersize = buffersize
        self.contour_name = EddiesObservations.intern(intern, public_label=True)
        self.xname, self.yname = EddiesObservations.intern(intern)
        self.memory = memory

    def load_contour(self, filename):
        if filename not in self.DATA:
            if len(self.FLIST) > self.buffersize:
                self.DATA.pop(self.FLIST.pop(0))
            if self.memory:
                # Only if netcdf
                with open(filename, "rb") as h:
                    e = EddiesObservations.load_file(h, include_vars=self.contour_name)
            else:
                e = EddiesObservations.load_file(
                    filename, include_vars=self.contour_name
                )
            self.FLIST.append(filename)
            self.DATA[filename] = e[self.xname], e[self.yname]
        return self.DATA[filename]


class NetworkObservations(EddiesObservations):

    __slots__ = tuple()

    NOGROUP = 0

    @property
    def elements(self):
        elements = super().elements
        elements.extend(["track", "segment", "next_obs", "previous_obs"])
        return list(set(elements))

    @classmethod
    def from_split_network(cls, group_dataset, indexs, **kwargs):
        """
        Build a NetworkObservations object with Group dataset and indexs

        :param TrackEddiesObservations group_dataset: Group dataset
        :param indexs: result from split_network
        return NetworkObservations
        """
        index_order = indexs.argsort(order=("group", "track", "time"))
        network = cls.new_like(group_dataset, len(group_dataset), **kwargs)
        network.sign_type = group_dataset.sign_type
        for field in group_dataset.elements:
            if field not in network.elements:
                continue
            network[field][:] = group_dataset[field][index_order]
        network.segment[:] = indexs["track"][index_order]
        # n & p must be re-index
        n, p = indexs["next_obs"][index_order], indexs["previous_obs"][index_order]
        translate = empty(index_order.max() + 1, dtype="i4")
        translate[index_order] = arange(index_order.shape[0])
        m = n != -1
        n[m] = translate[n[m]]
        m = p != -1
        p[m] = translate[p[m]]
        network.next_obs[:] = n
        network.previous_obs[:] = p
        return network

    def relative(self, i_obs, order=2, direct=True, only_past=False, only_future=False):
        pass

    def only_one_network(self):
        """
        Raise a warning or error?
        if there are more than one network
        """
        # TODO
        pass

    def display_timeline(self, ax, event=False):
        """
        Must be call on only one network
        """
        self.only_one_network()
        j = 0
        line_kw = dict(
            ls="-",
            marker=".",
            markersize=6,
            zorder=1,
            lw=3,
        )
        for i, b0, b1 in self.iter_on("segment"):
            x = self.time[i]
            if x.shape[0] == 0:
                continue
            y = b0 * ones(x.shape)
            line = ax.plot(x, y, **line_kw, color=self.COLORS[j % self.NB_COLORS])[0]
            if event:
                event_kw = dict(color=line.get_color(), ls="-", zorder=1)
                i_n, i_p = (
                    self.next_obs[i.stop - 1],
                    self.previous_obs[i.start],
                )
                if i_n != -1:
                    ax.plot(
                        (x[-1], self.time[i_n]), (b0, self.segment[i_n]), **event_kw
                    )
                    ax.plot(x[-1], b0, color="k", marker=">", markersize=10, zorder=-1)
                if i_p != -1:
                    ax.plot((x[0], self.time[i_p]), (b0, self.segment[i_p]), **event_kw)
                    ax.plot(x[0], b0, color="k", marker="*", markersize=12, zorder=-1)
            j += 1

    def insert_virtual(self):
        pass


class Network:
    __slots__ = (
        "window",
        "filenames",
        "nb_input",
        "buffer",
        "memory",
    )

    NOGROUP = TrackEddiesObservations.NOGROUP

    def __init__(self, input_regex, window=5, intern=False, memory=False):
        """
        Class to group observations by network
        """
        self.window = window
        self.buffer = Buffer(window, intern, memory)
        self.filenames = glob(input_regex)
        self.filenames.sort()
        self.nb_input = len(self.filenames)
        self.memory = memory

    def get_group_array(self, results, nb_obs):
        """With a loop on all pair of index, we will label each obs with a group
        number
        """
        nb_obs = array(nb_obs, dtype="u4")
        day_start = nb_obs.cumsum() - nb_obs
        gr = empty(nb_obs.sum(), dtype="u4")
        gr[:] = self.NOGROUP

        merge_id = list()
        id_free = 1
        for i, j, ii, ij in results:
            gr_i = gr[slice(day_start[i], day_start[i] + nb_obs[i])]
            gr_j = gr[slice(day_start[j], day_start[j] + nb_obs[j])]
            # obs with no groups
            m = (gr_i[ii] == self.NOGROUP) * (gr_j[ij] == self.NOGROUP)
            nb_new = m.sum()
            gr_i[ii[m]] = gr_j[ij[m]] = arange(id_free, id_free + nb_new)
            id_free += nb_new
            # associate obs with no group with obs with group
            m = (gr_i[ii] != self.NOGROUP) * (gr_j[ij] == self.NOGROUP)
            gr_j[ij[m]] = gr_i[ii[m]]
            m = (gr_i[ii] == self.NOGROUP) * (gr_j[ij] != self.NOGROUP)
            gr_i[ii[m]] = gr_j[ij[m]]
            # case where 2 obs have a different group
            m = gr_i[ii] != gr_j[ij]
            if m.any():
                # Merge of group, ref over etu
                for i_, j_ in zip(ii[m], ij[m]):
                    merge_id.append((gr_i[i_], gr_j[j_]))

        gr_transfer = arange(id_free, dtype="u4")
        for i, j in merge_id:
            gr_i, gr_j = gr_transfer[i], gr_transfer[j]
            if gr_i != gr_j:
                apply_replace(gr_transfer, gr_i, gr_j)
        return gr_transfer[gr]

    def group_observations(self, **kwargs):
        results, nb_obs = list(), list()
        # To display print only in INFO
        display_iteration = logger.getEffectiveLevel() == logging.INFO
        for i, filename in enumerate(self.filenames):
            if display_iteration:
                print(f"{filename} compared to {self.window} next", end="\r")
            # Load observations with function to buffered observations
            xi, yi = self.buffer.load_contour(filename)
            # Append number of observations by filename
            nb_obs.append(xi.shape[0])
            for j in range(i + 1, min(self.window + i + 1, self.nb_input)):
                xj, yj = self.buffer.load_contour(self.filenames[j])
                ii, ij = bbox_intersection(xi, yi, xj, yj)
                m = vertice_overlap(xi[ii], yi[ii], xj[ij], yj[ij], **kwargs) > 0.2
                results.append((i, j, ii[m], ij[m]))
        if display_iteration:
            print()

        gr = self.get_group_array(results, nb_obs)
        logger.info(
            f"{(gr == self.NOGROUP).sum()} alone / {len(gr)} obs, {len(unique(gr))} groups"
        )
        return gr

    def build_dataset(self, group):
        nb_obs = group.shape[0]
        model = EddiesObservations.load_file(self.filenames[-1], raw_data=True)
        eddies = TrackEddiesObservations.new_like(model, nb_obs)
        eddies.sign_type = model.sign_type
        # Get new index to re-order observation by group
        new_i = get_next_index(group)
        display_iteration = logger.getEffectiveLevel() == logging.INFO
        elements = eddies.elements

        i = 0
        for filename in self.filenames:
            if display_iteration:
                print(f"Load {filename} to copy", end="\r")
            if self.memory:
                # Only if netcdf
                with open(filename, "rb") as h:
                    e = EddiesObservations.load_file(h, raw_data=True)
            else:
                e = EddiesObservations.load_file(filename, raw_data=True)
            stop = i + len(e)
            sl = slice(i, stop)
            for element in elements:
                eddies[element][new_i[sl]] = e[element]
            i = stop
        if display_iteration:
            print()
        eddies = eddies.add_fields(("track",))
        eddies.track[new_i] = group
        return eddies


@njit(cache=True)
def get_next_index(gr):
    """Return for each obs index the new position to join all group"""
    nb_obs_gr = bincount(gr)
    i_gr = nb_obs_gr.cumsum() - nb_obs_gr
    new_index = empty(gr.shape, dtype=uint32)
    for i, g in enumerate(gr):
        new_index[i] = i_gr[g]
        i_gr[g] += 1
    return new_index


@njit(cache=True)
def apply_replace(x, x0, x1):
    nb = x.shape[0]
    for i in range(nb):
        if x[i] == x0:
            x[i] = x1
