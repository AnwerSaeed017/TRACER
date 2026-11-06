"""Business capabilities (bounded contexts). Each is domain/ (+ application/, adapters/ later).

Modules never import each other. They share only the kernel in ``tracer.shared`` (ids,
money, quantity, time), and refer to each other's aggregates by id.
"""
