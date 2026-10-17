using System.Net;
using System.Text.Json.Nodes;

namespace Mole.API.Tests;

/// <summary>A tunnel computation with the real MOLE: set MOLE2 to the mole2 executable, otherwise the test is skipped</summary>
public class MoleTests
{
    private static readonly string? Mole = Environment.GetEnvironmentVariable("MOLE2");

    [Fact]
    public async Task Tunnels_of_an_uploaded_structure()
    {
        Assert.SkipWhen(Mole == null, "set MOLE2 to the mole2 executable");
        using var api = new Api(mole: Mole);
        var id = await api.LoadAsync();

        var parameters = await api.Client.GetStringAsync("/inputs/1tqn_point_channels.json"); // the example of the documentation
        Assert.Equal(HttpStatusCode.OK, (await api.SubmitAsync(id, parameters)).Status);
        var status = await api.WaitAsync(id, 1);
        Assert.Equal("Finished", (string)status["Status"]!);

        var data = JsonNode.Parse(await api.Client.GetStringAsync($"/Data/{id}?submitId=1"))!;
        Assert.Contains(data["Channels"]!.AsObject(), channels => channels.Value is JsonArray { Count: > 0 }); // tunnels, paths or pores
        foreach (var format in new[] { "report", "pymol", "vmd", "chimera", "pdb", "molecule" })
        {
            var response = await api.Client.GetAsync($"/Data/{id}?submitId=1&format={format}");
            Assert.True(response.StatusCode == HttpStatusCode.OK, format);
            Assert.True((await response.Content.ReadAsByteArrayAsync()).Length > 100, format);
        }
        Assert.Equal(HttpStatusCode.OK, (await api.Client.GetAsync($"/Data/{id}?submitId=0&format=json")).StatusCode); // the cavities of the load

        var (_, info) = await api.GetAsync($"/CompInfo/{id}");
        Assert.Equal("Finished", (string)info["Submissions"]![0]!["Status"]!);
        var (csa, _) = await api.GetAsync($"/CSA/{id}");
        Assert.Equal(HttpStatusCode.OK, csa);
    }
}
