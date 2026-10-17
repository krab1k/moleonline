using System.Net;
using System.Text;
using Mole.API.Models;
using Microsoft.Extensions.Options;
using Newtonsoft.Json;

namespace Mole.API.Tests;

/// <summary>HTTP statuses of requests that do not depend on a running computation (README, Endpoints)</summary>
public class RequestTests
{
    private static async Task<HttpStatusCode> Send(Api api, string method, string path)
    {
        using var request = new HttpRequestMessage(new HttpMethod(method), path);
        if (path.StartsWith("/Submit/")) request.Content = new StringContent("{}", Encoding.UTF8, "application/json");
        return (await api.Client.SendAsync(request)).StatusCode;
    }

    private static readonly (string Method, string Path)[] ComputationEndpoints =
    [
        ("GET", "/Status/{0}"), ("GET", "/CompInfo/{0}"), ("GET", "/CSA/{0}"), ("GET", "/Data/{0}"),
        ("POST", "/Submit/Mole/{0}"), ("POST", "/Submit/Pores/{0}"), ("POST", "/Kill/{0}"), ("DELETE", "/Delete/{0}"),
    ];

    [Fact]
    public async Task Unknown_computation_is_404_everywhere()
    {
        using var api = new Api();
        foreach (var (method, path) in ComputationEndpoints)
            Assert.True(await Send(api, method, string.Format(path, "nope")) == HttpStatusCode.NotFound, $"{method} {path}");
    }

    [Fact]
    public async Task Deleted_computation_is_410_everywhere()
    {
        using var api = new Api();
        var id = await api.LoadAsync();
        Assert.Equal(HttpStatusCode.OK, await Send(api, "DELETE", $"/Delete/{id}"));
        foreach (var (method, path) in ComputationEndpoints)
            Assert.True(await Send(api, method, string.Format(path, id)) == HttpStatusCode.Gone, $"{method} {path}");
    }

    [Fact]
    public async Task Stale_copy_does_not_undo_deletion()
    {
        // a download or a run holds its own copy of the computation and may save it after a delete
        using var api = new Api();
        var id = await api.LoadAsync();
        var stale = JsonConvert.DeserializeObject<Computation>(File.ReadAllText(Path.Combine(api.WorkingDirectory, id, "_object.json")))!;
        Assert.Equal(HttpStatusCode.OK, (await api.Client.DeleteAsync($"/Delete/{id}")).StatusCode);

        stale.ChangeStatus(ComputationStatus.FailedInitialization, "late");
        Assert.Equal(HttpStatusCode.Gone, (await api.GetAsync($"/Status/{id}")).Status);
    }

    [Theory]
    [InlineData("/Init/1tqn?assembly=a-b", HttpStatusCode.BadRequest)] // refused before PDBe is asked
    [InlineData("/Init/1tqn?assembly=12345678901", HttpStatusCode.BadRequest)]
    [InlineData("/Init/Pores/1tqn", HttpStatusCode.NotFound)] // replaced by ?assembly=preferred
    public async Task Load_from_PDBe_is_refused(string path, HttpStatusCode expected)
    {
        using var api = new Api();
        Assert.Equal(expected, (await api.Client.PostAsync(path, null)).StatusCode);
    }

    [Theory]
    [InlineData("not json", "Input format error")]
    [InlineData("""{"Cavity": {"ProbeRadius": 100}}""", "Probe radius must be between")]
    [InlineData("""{"Input": {"ReadAllModels": true}}""", "Input format error")] // only the first model is used, the option is gone
    public async Task Invalid_tunnel_parameters_are_400(string parameters, string message)
    {
        using var api = new Api();
        var id = await api.LoadAsync();
        var (status, body) = await api.SubmitAsync(id, parameters);
        Assert.Equal(HttpStatusCode.BadRequest, status);
        Assert.Contains(message, (string)body["ErrorMsg"]!);
    }

    [Fact]
    public async Task Unknown_pore_parameter_is_400()
    {
        using var api = new Api();
        var id = await api.LoadAsync();
        var response = await api.Client.PostAsync($"/Submit/Pores/{id}", new StringContent("""{"IsBetaStructure": true}""", Encoding.UTF8, "application/json"));
        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    [Fact]
    public async Task Data_requests()
    {
        using var api = new Api();
        var id = await api.LoadAsync();
        Assert.Equal(HttpStatusCode.OK, (await api.SubmitAsync(id)).Status);
        Assert.Equal("Finished", (string)(await api.WaitAsync(id, 1))["Status"]!); // the fake MOLE writes no results

        Assert.Equal(HttpStatusCode.BadRequest, (await api.GetAsync($"/Data/{id}?submitId=1&format=xyz")).Status);
        Assert.Equal(HttpStatusCode.NotFound, (await api.GetAsync($"/Data/{id}?submitId=99")).Status);
        Assert.Equal(HttpStatusCode.NotFound, (await api.GetAsync($"/Data/{id}?submitId=1")).Status); // no results
        Assert.Equal(HttpStatusCode.NotFound, (await api.GetAsync($"/Data/{id}?format=membrane")).Status); // not a pore computation
        Assert.Equal(HttpStatusCode.NotFound, (await api.GetAsync($"/Status/{id}?submitId=99")).Status);
    }

    [Fact]
    public async Task Loads_and_submissions_are_logged()
    {
        using var api = new Api();
        var id = await api.LoadAsync();
        await api.SubmitAsync(id);
        await api.SubmitAsync("x\ny%0Az"); // the id comes from the request
        await api.UploadAsync("empty.pdb", []); // refused, no computation
        await api.Client.PostAsync("/Init/1tqn?assembly=a-b", null);

        string[] expected = [$"Init Upload {id}", $"Submit Mole {id}", "Submit Mole x?y?z", "Init Upload -", "Init PDB -"];
        Assert.Equal(expected.Order(StringComparer.Ordinal), (await LoggedRequests(api, expected.Length)).Order(StringComparer.Ordinal)); // appended in any order
    }

    /// <summary>Request, kind and id of each line, once the log has <c>count</c> complete lines (it is appended in the background)</summary>
    private static async Task<string[]> LoggedRequests(Api api, int count)
    {
        var log = Path.Combine(api.WorkingDirectory, "Mole_api_IP_log.csv");
        for (var end = DateTime.UtcNow.AddSeconds(10); DateTime.UtcNow < end; await Task.Delay(50))
        {
            var text = File.Exists(log) ? File.ReadAllText(log) : "";
            if (text.EndsWith('\n') && text.Count(c => c == '\n') == count)
                return [.. text.TrimEnd('\n').Split('\n').Select(line => line.Split(' ')).Select(f => $"{f[2]} {f[^2]} {f[^1]}")];
        }
        throw new TimeoutException($"{log} has not {count} complete lines.");
    }

    [Fact]
    public async Task Corrupt_status_file_is_500_with_a_report()
    {
        using var api = new Api();
        var id = await api.LoadAsync();
        File.WriteAllText(Path.Combine(api.WorkingDirectory, id, "_object.json"), "{x");
        var (status, body) = await api.GetAsync($"/Status/{id}");
        Assert.Equal(HttpStatusCode.InternalServerError, status);
        Assert.Equal("Error", (string)body["Status"]!);
    }

    [Fact]
    public async Task MOLE_without_a_version_stops_the_API()
    {
        // the reader runs when the API starts; its error stops the start like a wrong setting (checked here directly,
        // a failed start in the test host shows only as a disposed object)
        var directory = Directory.CreateTempSubdirectory("mole-api-tests-").FullName;
        var mole = Path.Combine(directory, "mole");
        File.WriteAllText(mole, "#!/bin/sh\necho 'not MOLE'\n");
        File.SetUnixFileMode(mole, UnixFileMode.UserRead | UnixFileMode.UserExecute);
        var reader = new Utils.MoleVersionReader(Options.Create(new Config { MOLE = mole }), new Utils.MoleVersion());
        var error = await Assert.ThrowsAsync<OptionsValidationException>(() => reader.StartAsync(default));
        Assert.Contains("did not report its version, its first line is \"not MOLE\"", error.Message);
        Directory.Delete(directory, recursive: true);
    }

    [Fact]
    public async Task Documentation_and_version()
    {
        using var api = new Api();
        Assert.Contains("MOLE", await api.Client.GetStringAsync("/"));
        var (status, version) = await api.GetAsync("/Version");
        Assert.Equal(HttpStatusCode.OK, status);
        Assert.NotNull(version["APIVersion"]);
        Assert.Equal(Api.MoleVersion, (string)version["MoleVersion"]!);
    }
}
