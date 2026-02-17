import docker

client = docker.from_env()


def containers():
    return client.containers.list()


def container_by_name(name):
    for cnt in containers():
        if cnt.name == name:
            return cnt
