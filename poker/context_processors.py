from . import pollstatus


def poll_problem(request):
    return {"poll_problem": pollstatus.problem()}
