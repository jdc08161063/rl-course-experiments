import numpy as np
from scipy.misc import imresize
import gym
from gym.core import ObservationWrapper, Wrapper
from gym.spaces.box import Box
from gym.wrappers import SkipWrapper, TimeLimit
from copy import copy
import collections

try:
    import ppaquette_gym_doom
    from ppaquette_gym_doom.wrappers.action_space import ToDiscrete
except ImportError:
    print("no doom envs")


Transition = collections.namedtuple(
    "Transition",
    ["state", "action", "reward", "next_state", "done"])


class PreprocessImage(ObservationWrapper):
    def __init__(self, env, height=64, width=64, grayscale=True, crop=None):
        """
        A gym wrapper that crops, scales image into the desired shapes and optionally grayscales it.
        """
        super(PreprocessImage, self).__init__(env)
        self.img_size = (height, width)
        self.grayscale = grayscale
        no_crop = lambda img: img
        self.crop = crop or no_crop 

        n_colors = 1 if self.grayscale else 3
        self.observation_space = Box(0.0, 1.0, [height, width, n_colors])

    def _observation(self, img):
        """what happens to the observation"""
        img = self.crop(img)
        img = imresize(img, self.img_size)
        if self.grayscale:
            img = img.mean(-1, keepdims=True)
        img = img.astype('float32') / 255.
        return img


class FrameBuffer(Wrapper):
    def __init__(self, env, n_frames=4, reshape_fn=None):
        """A gym wrapper that returns last n_frames observations as a single observation.
        Useful for games like Atari and Doom with screen as input."""
        super(FrameBuffer, self).__init__(env)
        self.framebuffer = np.zeros([n_frames, ] + list(env.observation_space.shape))

        # now, hacky auto-reshape fn
        if reshape_fn is None:
            shape_dims = list(range(len(self.framebuffer.shape)))
            shape_dims = shape_dims[1:] + [shape_dims[0]]

            result_shape = list(env.observation_space.shape)
            if len(result_shape) == 1:
                # so, its linear env
                result_shape += [1]
            result_shape[-1] = result_shape[-1] * n_frames

            reshape_fn = lambda x: np.transpose(x, shape_dims).reshape(result_shape)

        self.reshape_fn = reshape_fn
        self.observation_space = Box(0.0, 1.0, self.reshape_fn(self.framebuffer).shape)

    def reset(self):
        """resets breakout, returns initial frames"""
        self.framebuffer = np.zeros_like(self.framebuffer)
        self.update_buffer(self.env.reset())
        return self.reshape_fn(self.framebuffer)

    def step(self, action):
        """plays breakout for 1 step, returns 4-frame buffer"""
        new_obs, r, done, info = self.env.step(action)
        self.update_buffer(new_obs)
        return self.reshape_fn(self.framebuffer), r, done, info

    def update_buffer(self, obs):
        """push new observation to the buffer, remove the earliest one"""
        self.framebuffer = np.vstack([obs[None], self.framebuffer[:-1]])


class EnvPool(Wrapper):
    """
        Typical EnvPool, that does not care about done envs.
    """

    def __init__(self, env, n_envs=16, autoreload_envs=False):
        super(EnvPool, self).__init__(env)
        self.initial_env = env
        self.n_envs = n_envs
        self.env_shape = env.observation_space.shape
        self.envs = []
        self.recreate_envs()
        self.reset()

    def recreate_envs(self):
        self.close()
        self.envs = np.array([copy(self.initial_env) for _ in range(self.n_envs)])

    def reset(self):
        self._states = np.zeros(shape=(self.n_envs,) + tuple(self.env_shape), dtype=np.float32)
        self._rewards = np.zeros(shape=self.n_envs, dtype=np.float32)
        self._dones = np.zeros(shape=self.n_envs, dtype=np.bool)
        for i, env in enumerate(self.envs):
            self._states[i] = env.reset()
        return self._states.copy()

    def step(self, actions):

        for i, (action, env) in enumerate(zip(actions, self.envs)):
            new_s, r, done, _ = env.step(action)
            self._rewards[i] = r
            self._dones[i] = done
            if not done:
                self._states[i] = new_s
            else:
                self._states[i] = env.reset()
        return self._states.copy(), self._rewards.copy(), self._dones.copy(), None

    def close(self):
        for env in self.envs:
            env.close()

    def pool_states(self):
        return self._states.copy()


def make_env(env_name, n_games=1, episode_limit=None, n_frames=1, autoreload_envs=False):
    env = gym.make(env_name) if episode_limit is None else gym.make(env_name).env
    env = FrameBuffer(env, n_frames=n_frames) if n_frames > 1 else env
    if episode_limit is not None:
        env = TimeLimit(env, max_episode_steps=episode_limit)
    return EnvPool(env, n_games, autoreload_envs) if n_games > 0 else env


def make_image_env(
        env_name, n_games=1, episode_limit=None,
        n_frames=1, autoreload_envs=False,
        width=64, height=64,
        grayscale=True, crop=None):
    env = gym.make(env_name) if episode_limit is None else gym.make(env_name).env
    if "ppaquette" in env_name:
        env = SkipWrapper(4)(ToDiscrete("minimal")(env))
    env = PreprocessImage(env, width=width, height=height, grayscale=grayscale, crop=crop)
    env = FrameBuffer(env, n_frames=n_frames) if n_frames > 1 else env
    if episode_limit is not None:
        env = TimeLimit(env, max_episode_steps=episode_limit)
    return EnvPool(env, n_games, autoreload_envs) if n_games > 0 else env


def make_env_wrapper(make_env_fn, params):
    def wrapper(env, n_games, episode_limit=None):
        return make_env_fn(env, n_games, episode_limit=episode_limit, **params)

    return wrapper
