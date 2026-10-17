using System.Net;
using System.Text.Json.Nodes;

namespace Mole.API.Tests;

/// <summary>Loads and computations run by the process runner, with a fake MOLE: statuses, slots, stopping, failures</summary>
public class RunnerTests
{
    // the fake MOLE marks that it started (next to its input), then runs until stopped
    private const string Started = "touch \"$(dirname \"$1\")/started\"";
    private const string Forever = Started + "; " + Api.UntilTestEnds;

    private static async Task<(HttpStatusCode Status, string Message)> Submit(Api api, string id)
    {
        var (status, body) = await api.SubmitAsync(id);
        return (status, (string)body["ErrorMsg"]!);
    }

    [Fact]
    public async Task Computation_runs_and_finishes()
    {
        using var api = new Api();
        var id = await api.LoadAsync();
        var (status, body) = await api.SubmitAsync(id);
        Assert.Equal(HttpStatusCode.OK, status);
        Assert.Equal("Running", (string)body["Status"]!);
        Assert.Equal(1, (int)body["SubmitId"]!);
        Assert.Equal("Finished", (string)(await api.WaitAsync(id, 1))["Status"]!);
        Assert.Equal(2, (int)(await api.SubmitAsync(id)).Body["SubmitId"]!);
    }

    [Fact]
    public async Task Times_and_MOLE_version_are_recorded()
    {
        using var api = new Api();
        var id = await api.LoadAsync();
        await api.SubmitAsync(id);
        await api.WaitAsync(id, 1);

        var saved = JsonNode.Parse(File.ReadAllText(Path.Combine(api.WorkingDirectory, id, "_object.json")))!;
        var created = (DateTime)saved["Created"]!;
        var loaded = (DateTime)saved["Loaded"]!;
        var unit = saved["ComputationUnits"]![0]!;
        var (submitted, finished) = ((DateTime)unit["Submitted"]!, (DateTime)unit["Finished"]!);
        Assert.True(created <= loaded && loaded <= submitted && submitted <= finished, $"{created:O} {loaded:O} {submitted:O} {finished:O}");
        Assert.Equal(DateTimeKind.Utc, created.Kind);
        Assert.Equal(new Utils.Version().MoleVersion, (string)unit["MoleVersion"]!);
    }

    [Fact]
    public async Task Busy_computation_refuses_and_stops()
    {
        using var api = new Api(run: Forever);
        var id = await api.LoadAsync();
        Assert.Equal(HttpStatusCode.OK, (await Submit(api, id)).Status);
        await api.WaitForFileAsync(Path.Combine(api.WorkingDirectory, id, "1", "started"));

        var busy = await Submit(api, id);
        Assert.Equal(HttpStatusCode.Conflict, busy.Status);
        Assert.Contains("still running", busy.Message);

        var (killed, report) = await api.PostAsync($"/Kill/{id}");
        Assert.Equal(HttpStatusCode.OK, killed);
        Assert.Equal("Aborted", (string)report["Status"]!);
        Assert.Equal(HttpStatusCode.Conflict, (await api.PostAsync($"/Kill/{id}")).Status); // nothing runs now

        var (again, next) = await api.SubmitAsync(id); // a stopped computation does not block the structure
        Assert.Equal(HttpStatusCode.OK, again);
        Assert.Equal(2, (int)next["SubmitId"]!);
        await api.WaitForFileAsync(Path.Combine(api.WorkingDirectory, id, "2", "started"));
        Assert.Equal(HttpStatusCode.OK, (await api.PostAsync($"/Kill/{id}")).Status);
    }

    [Fact]
    public async Task All_slots_busy_is_503()
    {
        using var api = new Api(run: Forever, slots: 1);
        var first = await api.LoadAsync();
        var second = await api.LoadAsync();
        Assert.Equal(HttpStatusCode.OK, (await Submit(api, first)).Status);
        await api.WaitForFileAsync(Path.Combine(api.WorkingDirectory, first, "1", "started")); // the slot is taken

        var (status, message) = await Submit(api, second);
        Assert.Equal(HttpStatusCode.ServiceUnavailable, status);
        Assert.Contains("heavy load", message);
        await api.PostAsync($"/Kill/{first}");
    }

    [Fact]
    public async Task Stopped_load_refuses_computations()
    {
        using var api = new Api(load: Forever);
        var (_, body) = await api.UploadAsync("1tqn.cif", Api.Structure);
        var id = (string)body["ComputationId"]!;
        await api.WaitForFileAsync(Path.Combine(api.WorkingDirectory, id, "0", "started"));

        var (killed, report) = await api.PostAsync($"/Kill/{id}");
        Assert.Equal(HttpStatusCode.OK, killed);
        Assert.Equal("FailedInitialization", (string)report["Status"]!);
        Assert.Equal("Loading of the structure was stopped.", (string)report["ErrorMsg"]!);
        Assert.Equal(HttpStatusCode.Conflict, (await Submit(api, id)).Status);
    }

    [Fact]
    public async Task Failed_load_refuses_computations()
    {
        using var api = new Api(load: "exit 3");
        var (_, body) = await api.UploadAsync("1tqn.cif", Api.Structure);
        var id = (string)body["ComputationId"]!;
        var status = await api.WaitAsync(id);
        Assert.Equal("FailedInitialization", (string)status["Status"]!);
        Assert.Equal("The computation failed with exit code 3.", (string)status["ErrorMsg"]!);

        var (refused, message) = await Submit(api, id);
        Assert.Equal(HttpStatusCode.Conflict, refused);
        Assert.Contains("could not be loaded", message);
    }

    [Theory]
    [InlineData("exit 7", "The computation failed with exit code 7.")]
    [InlineData("kill -9 $$", "most likely it ran out of memory")] // exit code 137, as the kernel's OOM killer
    [InlineData("echo 'Invalid structure' >&2", "Invalid structure")] // MOLE reports errors on stderr, with exit code 0
    public async Task Failed_computation_is_an_error(string run, string message)
    {
        using var api = new Api(run: run);
        var id = await api.LoadAsync();
        await api.SubmitAsync(id);
        var status = await api.WaitAsync(id, 1);
        Assert.Equal("Error", (string)status["Status"]!);
        Assert.Contains(message, (string)status["ErrorMsg"]!);
        Assert.Equal(HttpStatusCode.OK, (await Submit(api, id)).Status); // the structure is still loaded
    }

    [Fact]
    public async Task Stopping_kills_the_whole_process_tree()
    {
        // Pores starts MEMEMBED and MOLE as children; the child must not keep running after a kill
        using var api = new Api(run: $"({Api.UntilTestEnds}) & echo $! > \"$(dirname \"$1\")/child\"; {Started}; wait");
        var id = await api.LoadAsync();
        await api.SubmitAsync(id);
        var directory = Path.Combine(api.WorkingDirectory, id, "1");
        await api.WaitForFileAsync(Path.Combine(directory, "started"));
        var child = int.Parse(File.ReadAllText(Path.Combine(directory, "child")));
        Assert.True(Running(child));

        await api.PostAsync($"/Kill/{id}");
        for (var end = DateTime.UtcNow.AddSeconds(5); Running(child) && DateTime.UtcNow < end;) await Task.Delay(50);
        Assert.False(Running(child));
    }

    /// <summary>A process that exists and is not a zombie (killed, waiting to be reaped)</summary>
    private static bool Running(int pid)
    {
        try { return !File.ReadAllText($"/proc/{pid}/stat").Split(' ')[2].Equals("Z"); }
        catch (IOException) { return false; }
    }
}
